import asyncio
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from loguru import logger
from pydantic import BaseModel

from mlla.modules.sanitizer import get_sanitizer_list, get_sanitizer_prompt

from ...prompts.bcda_experimental import (
    RETRY_GUIDANCE,
    SANITIZER_VALIDATION_SYSTEM,
    SINK_DETECT_SYSTEM,
    SINK_DETECT_SYSTEM_WITH_DIFF,
    SINK_RETRY_MSG,
)
from ...prompts.llm import ASK_AND_REPEAT_UNTIL_MSG
from .. import instrument_line
from ..cg import FuncInfo
from ..context import GlobalContext
from ..diff_analyzer import extract_diffs_in_range
from ..llm import LLM
from ..llm_cache import LLMCache, LLMCacheFactory

# from ..messages import add_cache_control
from . import SinkDetectReport


class SanitizerValidationReport(BaseModel):
    sanitizer_type: str


def gen_sanitizer_verifier(
    sanitizer_candidates: List[str],
) -> Callable[[BaseMessage | SanitizerValidationReport], str]:
    """Validate the sanitizer type"""

    def _validate_sanitizer_type(response) -> str:
        if isinstance(response, SanitizerValidationReport):
            content = response.sanitizer_type
        else:
            content = response.content

        if content in sanitizer_candidates:
            return content
        else:
            raise ValueError(
                f"Invalid sanitizer type: {content} is not in {sanitizer_candidates}"
            )

    return _validate_sanitizer_type


def validate_sanitizers_type(
    llm: LLM,
    returned_sanitizers: list[str],
    sanitizer_candidates: List[str],
    analysis_msg: str,
) -> list[str]:
    """Validate the sanitizer type"""
    if all(s in sanitizer_candidates for s in returned_sanitizers):
        return returned_sanitizers
    else:
        refined_sanitizer_list = []
        for sanitizer in returned_sanitizers:
            if sanitizer in sanitizer_candidates:
                refined_sanitizer_list.append(sanitizer)
            else:
                system_msg = SANITIZER_VALIDATION_SYSTEM.format(
                    vulnerability_description=analysis_msg,
                    sanitizer_types=sanitizer_candidates,
                )

            human_msg = ASK_AND_REPEAT_UNTIL_MSG.format(
                response_str=sanitizer,
                e=ValueError(
                    f"The sanitizer type {sanitizer} is not in {sanitizer_candidates}"
                ),
            )

            messages = [SystemMessage(system_msg), HumanMessage(human_msg)]
            response = llm.ask_and_repeat_until(
                gen_sanitizer_verifier(sanitizer_candidates),
                messages,
                sanitizer,
                max_retries=2,
                try_with_error=True,
            )

            refined_sanitizer_list.append(response)

        return refined_sanitizer_list


# Visitor pattern for traversing function graphs
class BCDAVisitor(ABC):
    @abstractmethod
    def visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        pass

    @abstractmethod
    async def async_visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        pass


class FuncGraph:
    def __init__(
        self,
        root: FuncInfo,
        visited_nodes: dict[str, FuncInfo],
    ):
        self.root = root
        self.visited_nodes = visited_nodes

    def traverse(self, visitor: BCDAVisitor):
        def _traverse(node: FuncInfo):
            if not node.func_body:
                for child in node.children:
                    _traverse(child)
                return

            visitor.visit(node)

            for child in node.children:
                _traverse(child)

        _traverse(self.root)

    async def async_traverse(self, visitor: BCDAVisitor):
        async def _traverse(node: FuncInfo):
            if not node.func_body:
                tasks = [_traverse(child) for child in node.children]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(f"Task failed: {result}")
                        import traceback

                        tb_lines = traceback.format_exception(
                            type(result), result, result.__traceback__
                        )
                        logger.warning("".join(tb_lines))
                        continue
                return

            # First

            async def process_node():
                try:
                    await visitor.async_visit(node, self.visited_nodes)
                except Exception as e:
                    logger.warning(f"Error in async_traverse: {e}")

            node_task = asyncio.create_task(process_node())
            children_tasks = [_traverse(child) for child in node.children]
            results = await asyncio.gather(
                node_task, *children_tasks, return_exceptions=True
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Task failed: {result}")
                    import traceback

                    tb_lines = traceback.format_exception(
                        type(result), result, result.__traceback__
                    )
                    logger.warning("".join(tb_lines))

        await _traverse(self.root)


# Visitors for different analysis tasks
class SinkDetectVisitor(BCDAVisitor):
    llm: LLM
    max_retries: int

    def __init__(self, gc: GlobalContext):
        self.llm = LLM(
            model="o3-mini",
            config=gc,
            output_format=SinkDetectReport,
        )
        self.llm_sanitizer_validator = LLM(
            model="gpt-4.1-mini", config=gc, output_format=SanitizerValidationReport
        )
        self.max_retries = 2
        self.gc = gc
        cache_config = {
            "kind": "redis",
            "redis_client": self.gc.redis,
            "hash_key": f"bcda_sd_cache::{self.gc.cp.name}",
        }
        self.sink_detect_cache: LLMCache = LLMCacheFactory.create(cache_config)
        self.sanitizer_type = self.gc.get_sanitizer_type()

    def visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        # This function is not used.
        # logger.debug(
        #     f"Analyzing function for sinks: {node.func_name} in {node.file_path}"
        # )
        if not node.func_body:
            return

        asyncio.run(self.async_visit(node, cache))

    async def async_visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        node_key = node.create_tag(verbose=False)
        if node_key in cache:
            node.sink_detector_report = cache[node_key].sink_detector_report
            return

        if not node.func_body:
            return

        logger.debug(
            f"Analyzing function for sinks: {node.func_location.func_name} in"
            f" {node.func_location.file_path}"
        )

        cache_key = f"bcda_sd::{node.create_tag(verbose=False)}"
        redis_response = self.sink_detect_cache.get(cache_key)
        if redis_response:
            try:
                report = SinkDetectReport.model_validate_json(redis_response)
                node.sink_detector_report = report
                logger.debug(f"[BCDA][SD] Found cached sink detect report: {cache_key}")
                return
            except Exception as e:
                logger.error(
                    f"[BCDA][SD] Error in validating cached sink detect report: {e}"
                )

        report = await self._analyze_for_sinks(node)
        node.sink_detector_report = report

        if report:
            self.sink_detect_cache.set(cache_key, report.model_dump_json())
            cache[node_key] = node

    async def _analyze_for_sinks(
        self, func_info: FuncInfo
    ) -> Optional[SinkDetectReport]:
        """Analyze function body for potential vulnerability sinks"""
        if not func_info.func_body:
            return None

        func_body, _ = instrument_line(func_info.func_body)
        if (
            func_info.interest_info
            and func_info.interest_info.is_interesting
            and func_info.interest_info.diff
        ):
            func_body += f"\n<diff>\n{func_info.interest_info.diff}\n</diff>"
            contain_diff = True
        else:
            contain_diff = False
        func_name = func_info.func_location.func_name

        for attempt in range(self.max_retries + 1):
            messages = self._prepare_messages(func_body, attempt, contain_diff)
            # add_cache_control(messages[0])
            response = await self.llm.ainvoke(messages, cache=True, cache_index=0)

            output_parser = PydanticOutputParser(pydantic_object=SinkDetectReport)
            try:
                report = output_parser.parse(response[-1].content)
                possible_sanitizers = (
                    get_sanitizer_list(self.sanitizer_type[0])
                    if self.sanitizer_type
                    else []
                )
                report.sanitizer_candidates = validate_sanitizers_type(
                    self.llm_sanitizer_validator,
                    report.sanitizer_candidates,
                    possible_sanitizers,
                    report.sink_analysis_message,
                )
                self._log_result(report, func_name)
                return report
            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning(
                        f"Error in sink detection attempt {attempt + 1}. Retrying..."
                    )
                    continue
                else:
                    logger.error(f"Error in sink detection: {e}")
                    return self._create_error_report()

        return self._create_error_report()

    def _prepare_messages(
        self, func_body: str, attempt: int, contain_diff: bool
    ) -> List:
        """Prepare messages for LLM based on attempt number"""
        sanitizer_prompt = get_sanitizer_prompt(
            self.sanitizer_type,
        )
        system_msg = (
            SINK_DETECT_SYSTEM.format(
                sanitizer_prompt=sanitizer_prompt, project_dir=self.gc.cp.cp_src_path
            )
            if not contain_diff
            else SINK_DETECT_SYSTEM_WITH_DIFF.format(
                sanitizer_prompt=sanitizer_prompt, project_dir=self.gc.cp.cp_src_path
            )
        )
        if attempt == 0:
            human_msg = func_body
            return [SystemMessage(system_msg), HumanMessage(human_msg)]
        else:
            retry_system_msg = f"{system_msg}\n{RETRY_GUIDANCE}"
            human_msg = SINK_RETRY_MSG.format(func_body=func_body)
            return [SystemMessage(retry_system_msg), HumanMessage(human_msg)]

    def _log_result(self, report: SinkDetectReport, func_name: str):
        """Log the result of sink detection"""
        if report.is_vulnerable:
            logger.info(
                f"Sink analysis result: {func_name}:"
                f" {report.sink_line}:{report.sink_line_number}"
            )
            logger.info(f"Sink analysis message: {report.sink_analysis_message}")
        else:
            logger.debug(f"No sink found for {func_name}")

    def _create_error_report(self) -> SinkDetectReport:
        """Create an error report when parsing fails"""
        return SinkDetectReport(
            sink_analysis_message="Error: Failed to parse analysis output properly.",
            is_vulnerable=False,
            sink_line="",
            sink_line_number=-1,
            sanitizer_candidates=[],
        )


class CachedResultHandlerVisitor(BCDAVisitor):
    def __init__(self, gc: GlobalContext):
        self.gc = gc

    def visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        asyncio.run(self.async_visit(node, cache))

    async def async_visit(self, node: FuncInfo, cache: dict[str, FuncInfo] = {}):
        await self._submit_to_bcda_if_vulnerable(node)
        self._handle_diff_analyzer_result(node)

    async def _submit_to_bcda_if_vulnerable(self, node: FuncInfo):
        from mlla.agents.mcga import get_current_cgs_from_redis

        if node.sink_detector_report and node.sink_detector_report.is_vulnerable:
            current_cgs = await get_current_cgs_from_redis(
                self.gc.cpua_target_fns, self.gc
            )
            if self.gc.candidate_queue:
                logger.info(f"Putting {len(current_cgs)} CGs to the queue")
                self.gc.candidate_queue.put({"CGs": current_cgs})

    def _handle_diff_analyzer_result(self, node: FuncInfo):
        diffs = self.gc.function_diffs

        if not node.func_location.file_path:
            return

        if node.func_location.file_path not in diffs:
            return

        _diffs = diffs[node.func_location.file_path]
        extract_diffs_in_range(
            _diffs,
            node.func_location.start_line,
            node.func_location.end_line,
            node.func_location.file_path,
            set_cg_included=True,
        )
