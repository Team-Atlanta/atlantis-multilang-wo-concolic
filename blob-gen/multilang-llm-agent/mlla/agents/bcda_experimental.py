import asyncio
import json
from asyncio import Task
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import MessagesState
from loguru import logger
from pydantic import BaseModel
from typing_extensions import Annotated, Dict, List, Optional, Tuple

from mlla.agents.bugcandidate_agent.path_extractor import ExtractedPath, set_done
from mlla.modules.sanitizer import get_sanitizer_prompt
from mlla.utils import normalize_func_name_for_ci
from mlla.utils.artifact_storage import store_artifact_files
from mlla.utils.cg import CG
from mlla.utils.state import merge_with_update

from ..prompts.bcda_experimental import (
    CLASSIFY_RETRY_MSG,
    CLASSIFY_SYSTEM,
    CLASSIFY_SYSTEM_WITH_DIFF,
    EXTRACT_PARTIAL_CONDITION_FORMAT,
    EXTRACT_PARTIAL_CONDITION_SYSTEM,
    PRUNE_UNNECESSARY_FORMAT,
    PRUNE_UNNECESSARY_SYSTEM,
    RETRY_GUIDANCE,
)
from ..utils import instrument_line
from ..utils.agent import BCDA, BaseAgentTemplate
from ..utils.analysis_interest import InterestPriority
from ..utils.bit import (
    AnalysisMessages,
    AnalyzedFunction,
    BugInducingThing,
    LocationInfo,
)
from ..utils.cg import FuncInfo
from ..utils.cg.visitor import SanitizerValidationReport, validate_sanitizers_type
from ..utils.context import GlobalContext
from ..utils.expanded_path import ExpandedPath
from ..utils.llm import LLM


class BugCandDetectAgentInputState(MessagesState):
    CGs: Annotated[Dict[str, List[CG]], merge_with_update]
    extracted_paths: List[ExtractedPath]


class BugCandDetectAgentOutputState(MessagesState):
    CGs: Annotated[Dict[str, List[CG]], merge_with_update]
    BITs: List[BugInducingThing]


class BugCandDetectAgentOverallState(
    BugCandDetectAgentInputState, BugCandDetectAgentOutputState
):
    pass


class LineInfo(BaseModel):
    func_name: str
    file_path: str
    line_number: int

    class Config:
        frozen = True


# Model definitions
class VulnerabilityReport(BaseModel):
    analysis_message: str
    possibily_vulnerable: bool
    vulnerable_line_str: str
    vulnerable_line_info: Optional[LineInfo]
    required_file_paths: List[str]
    sanitizer_type: str


class KeywordExtractionReport(BaseModel):
    keywords: List[str]


class KeyConditionReport(BaseModel):
    key_conditions: List[LineInfo]
    next_lines: List[LineInfo]


def validate_sanitizer_type(
    llm: LLM, sanitizer: str, sanitizer_candidates: List[str], analysis_msg: str
) -> str:
    """Validate a single sanitizer type"""
    return validate_sanitizers_type(
        llm, [sanitizer], sanitizer_candidates, analysis_msg
    )[0]


class BugCandDetectAgent(BaseAgentTemplate):
    llm: LLM
    visited_nodes: dict[str, FuncInfo] = {}
    async_visited_nodes: dict[str, asyncio.Future] = {}
    gc: GlobalContext

    def __init__(self, config: GlobalContext):
        ret_dir = config.RESULT_DIR / BCDA
        super().__init__(
            config,
            ret_dir,
            BugCandDetectAgentInputState,
            BugCandDetectAgentOutputState,
            BugCandDetectAgentOverallState,
        )

        # Define workflow graph
        self._setup_workflow_graph()

        # Initialize LLM models
        self.llm_o3 = LLM(model="o3-mini", config=config)
        self.llm_4o_key_cond = LLM(
            model="gpt-4.1", config=config, output_format=KeyConditionReport
        )
        self.llm_powerful = LLM(model="o3-mini", config=config)
        self.llm_prune_path = LLM(model="claude-sonnet-4-20250514", config=config)
        self.llm_partial_key_cond = LLM(
            model="o4-mini",
            config=config,
        )
        self.llm_extract_key_cond = LLM(
            model="claude-3-5-haiku-20241022",
            config=config,
            output_format=KeyConditionReport,
            max_tokens=8192,
        )
        # self.llm_extract_key_cond = LLM(
        #     model="gpt-4.1-mini",
        #     config=config,
        #     output_format=KeyConditionReport,
        # )
        # self.llm_extract_taken_lines = LLM(
        #     model="gpt-4.1-mini",
        #     config=config,
        #     output_format=TakenLineReport,
        # )

        self.llm_classify = LLM(
            model="claude-sonnet-4-20250514",
            config=config,
            output_format=VulnerabilityReport,
        )
        self.llm_sanitizer_validator = LLM(
            model="gpt-4.1-mini", config=config, output_format=SanitizerValidationReport
        )
        self.llm_lightweight_keyword_extracor = LLM(
            model="gpt-4o-mini", config=config, output_format=KeywordExtractionReport
        )
        # self.ret_file = self.ret_file.with_suffix(".json")
        self.language = config.cp.language
        self.gc = config

    def _setup_workflow_graph(self):
        """Set up the workflow graph for the agent"""
        self.builder.add_node("classifier", self.classify)

        self.builder.add_edge("preprocess", "classifier")
        self.builder.add_edge("classifier", "finalize")

    def pinpoint_line(
        self,
        candidate_path: list[FuncInfo],
        line_info: Optional[LineInfo],
        required_content: Optional[str] = None,
    ) -> LocationInfo | None:
        """Locate exact file and line information for a given line number in the path"""
        if not line_info:
            return None

        candidate = None

        for node in candidate_path:
            if (
                not node.func_body
                or normalize_func_name_for_ci(line_info.func_name)
                != normalize_func_name_for_ci(node.func_location.func_name)
                or (
                    line_info.line_number < node.func_location.start_line
                    or line_info.line_number > node.func_location.end_line
                )
            ):
                continue

            relative_line = line_info.line_number - node.func_location.start_line
            if (
                node.func_location.file_path
                and line_info.file_path == node.func_location.file_path
            ):
                if not required_content:
                    return self._create_location_info(node, relative_line)
                else:
                    result = self._handle_required_content(
                        node,
                        relative_line,
                        node.func_location.start_line,
                        required_content,
                    )

                    if result:
                        return result
                    else:
                        candidate = self._create_location_info(node, relative_line)
            elif node.func_location.file_path and (
                node.func_location.file_path.endswith(line_info.file_path)
                or Path(node.func_location.file_path).stem
                == Path(line_info.file_path).stem
            ):
                if not required_content:
                    candidate = self._create_location_info(node, relative_line)
                else:
                    result = self._handle_required_content(
                        node,
                        relative_line,
                        node.func_location.start_line,
                        required_content,
                    )

                    if result:
                        return result
                    else:
                        candidate = self._create_location_info(node, relative_line)

        if not candidate:
            logger.warning(f"Failed to pinpoint line: {line_info}")
            logger.info(f"Given required content: {required_content}")
            self._log_pinpoint_error(candidate_path)
        return candidate

    def _create_location_info(
        self, node: FuncInfo, relative_line: int, line_span: int = 0
    ) -> LocationInfo:
        """Create location information for a node and relative line"""
        return LocationInfo(
            func_name=node.func_location.func_name,
            file_path=str(node.func_location.file_path),
            start_line=node.func_location.start_line + relative_line,
            end_line=node.func_location.start_line + relative_line + line_span,
        )

    def _handle_required_content(
        self,
        node: FuncInfo,
        relative_line: int,
        cur_start_line: int,
        required_content: str,
    ) -> LocationInfo | None:
        """Handle cases where specific content is required"""
        if not node.func_body:
            return None
        start_line_number, end_line_number = self._find_multiline_content(
            node.func_body, required_content
        )
        if start_line_number is None or end_line_number is None:
            self._log_missing_content(
                node, cur_start_line, required_content, relative_line
            )
            return None
        else:
            if start_line_number != end_line_number:
                logger.warning(
                    f"Found multiple lines of required content: {required_content}"
                )

            return self._create_location_info(
                node, relative_line, end_line_number - start_line_number
            )

    def _find_multiline_content(
        self, func_body: str, required_content: str
    ) -> tuple[int | None, int | None]:
        """Find the multiline content in the function body"""

        # normalize the search string
        search_normalized = "".join(
            line.strip() for line in required_content.splitlines()
        )
        search_normalized = "".join(search_normalized.split()).lower()

        # logger.info(f"search_normalized: {search_normalized}")

        # split the target string into lines
        target_lines = func_body.splitlines()

        def _find_start_line() -> int | None:
            for j in range(len(target_lines), 0, -1):
                for i in range(j - 1, -1, -1):
                    # combine the lines into a single string
                    current_lines = target_lines[i:j]
                    # remove the whitespace and combine the lines
                    combined = "".join(line.strip() for line in current_lines)
                    # normalize the whitespace
                    combined_normalized = "".join(combined.split()).lower()

                    # logger.info(f"combined_normalized: {combined_normalized}")

                    if search_normalized in combined_normalized:
                        return i + 1
            return None

        def _find_end_line(start_line_number: int | None) -> int | None:
            if start_line_number is None:
                return None
            for i in range(start_line_number - 1, len(target_lines)):
                for j in range(i + 1, len(target_lines) + 1):
                    current_lines = target_lines[i:j]
                    combined = "".join(line.strip() for line in current_lines)
                    combined_normalized = "".join(combined.split()).lower()

                    if search_normalized in combined_normalized:
                        return j
            return None

        start_line_number = _find_start_line()
        end_line_number = _find_end_line(start_line_number)
        return start_line_number, end_line_number

    def _log_missing_content(
        self,
        node: FuncInfo,
        cur_start_line: int,
        required_content: str,
        relative_line: int,
    ):
        """Log warning when required content is missing"""
        if not node.func_body:
            return
        instrumented_body, _ = instrument_line(node.func_body, cur_start_line)
        logger.warning(
            f"Required content ({required_content}) does not exist in"
            f" {node.func_location.func_name}"
        )
        logger.warning(
            f"cur_start_line: {cur_start_line}, relative_line: {relative_line}"
        )
        logger.warning(f"Instrumented body: \n{instrumented_body}")

    def _log_pinpoint_error(self, path: list[FuncInfo]):
        """Log error when pinpointing fails"""
        logger.warning(
            "This should not happen. because the vulnerable/key condition lines should"
            f" be in the path: {[node.func_location.func_name for node in path]}"
        )
        for fi in path:
            if not fi.func_body:
                continue
            instrumented_body, _ = instrument_line(
                fi.func_body, fi.func_location.start_line
            )
            logger.warning(f"{instrumented_body}")

    async def _analyze_vulnerability(
        self,
        expanded_path: ExpandedPath,
        # sink_line: str,
        # sink_line_number: int,
        sanitizer_candidates: List[str],
        max_retries: int = 2,
    ) -> VulnerabilityReport:
        """Analyze code to determine if it contains a vulnerability"""
        # sanitizer_list = self.gc.cp.sanitizers
        # sanitizer_list = ["jazzer"] if self.language == "jvm" else sanitizer_list

        if self.language == "jvm":
            sanitizer_class = "jazzer"
            sanitizer_list = [
                sanitizer_class + "." + sanitizer_type
                for sanitizer_type in sanitizer_candidates
            ]
        elif len(self.gc.cp.sanitizers) == 0:
            sanitizer_class = "address"
            sanitizer_list = [
                sanitizer_class + "." + sanitizer_type
                for sanitizer_type in sanitizer_candidates
            ]
        else:
            sanitizer_zipped = zip(self.gc.cp.sanitizers, sanitizer_candidates)
            sanitizer_list = [
                sanitizer_class + "." + sanitizer_type
                for sanitizer_class, sanitizer_type in sanitizer_zipped
            ]

        sanitizer_prompt = get_sanitizer_prompt(sanitizer_list)

        for attempt in range(max_retries + 1):
            messages = self._prepare_vulnerability_messages(
                expanded_path, sanitizer_prompt, attempt
            )
            logger.debug(f"Vulnerability analysis attempt {attempt + 1}")

            try:
                # add_cache_control(messages[0])
                response = await self.llm_classify.ainvoke(messages)
                report: VulnerabilityReport = response[-1]

                report.sanitizer_type = validate_sanitizer_type(
                    self.llm_sanitizer_validator,
                    report.sanitizer_type,
                    sanitizer_candidates,
                    report.analysis_message,
                )
                self.last_analysis_message: str = report.analysis_message
                return report
            except Exception:
                if attempt < max_retries:
                    logger.warning(
                        f"Error in vulnerability analysis attempt {attempt + 1}."
                        " Retrying..."
                    )
                    continue
                else:
                    logger.error(
                        f"Failed to analyze vulnerability after {max_retries} attempts."
                    )
                    return self._create_vulnerability_error_report()

        # This should never be reached as we either return in try block or except block
        logger.error(f"Failed to analyze vulnerability after {max_retries} attempts.")
        return self._create_vulnerability_error_report()

    def _prepare_vulnerability_messages(
        self, expanded_path: ExpandedPath, sanitizer_prompt: str, attempt: int
    ) -> List:
        """Prepare messages for vulnerability analysis"""

        code_with_path = f"""Call flow:
{expanded_path.get_call_flow()}

Code:
{expanded_path.code_with_path()}"""

        sink_func = expanded_path.path_list[-1][0]
        if (
            not sink_func.sink_detector_report
            or not sink_func.sink_detector_report.is_vulnerable
        ):
            logger.warning(
                f"sink_func: {sink_func.func_location.func_name} is not vulnerable"
            )
            return []

        _num_tag_lines = 4
        _code_with_path = expanded_path.code_with_path([[sink_func]])
        _code_with_path_splitlines = _code_with_path.splitlines()

        # Sink line number is relative number to the start line of the sink function
        _sink_line_number = (
            sink_func.sink_detector_report.sink_line_number
            - sink_func.func_location.start_line
        )
        if _sink_line_number < 0:
            logger.warning(
                "sink_line_number is negative: "
                f"sink_line_number: {sink_func.sink_detector_report.sink_line_number}"
                f"sink_line: {sink_func.sink_detector_report.sink_line}"
            )
            _sink_line_number = sink_func.sink_detector_report.sink_line_number
        if len(_code_with_path_splitlines) > _sink_line_number + _num_tag_lines:
            _sink_line = _code_with_path_splitlines[_sink_line_number + _num_tag_lines]
        else:
            _sink_line = ""
            logger.warning(
                f"_code_with_path: {_code_with_path}\n"
                f"sink_detector_report: {sink_func.sink_detector_report}\n"
                f"func_location: {sink_func.func_location}\n"
            )

        if sink_func.sink_detector_report.sink_line.strip() in _sink_line:
            sink_line = _sink_line
        else:
            sink_line = (
                f"{sink_func.sink_detector_report.sink_line_number}:"
                f" {sink_func.sink_detector_report.sink_line}"
            )
            logger.warning(
                f"sink_line: {sink_func.sink_detector_report.sink_line} not found in"
                f" {_sink_line}"
                f'Therefore, "{sink_line}" is used as the sink line.'
            )
            logger.warning(f"sink function: {_code_with_path}")

        # Change prompt
        system_msg = (
            CLASSIFY_SYSTEM_WITH_DIFF.format(sanitizer_prompt=sanitizer_prompt)
            if expanded_path.contain_interesting_node()
            else CLASSIFY_SYSTEM.format(sanitizer_prompt=sanitizer_prompt)
        )
        if attempt == 0:
            human_msg = f"```{code_with_path.strip()}\n```\nSink line:\n{sink_line}"
        else:
            system_msg += f"\n{RETRY_GUIDANCE}"
            prev_analysis = (
                self.last_analysis_message
                if hasattr(self, "last_analysis_message")
                else "No previous analysis available"
            )
            human_msg = CLASSIFY_RETRY_MSG.format(
                code_with_path=code_with_path.strip(),
                sink_line=sink_line,
                prev_analysis=prev_analysis,
            )

        return [SystemMessage(system_msg), HumanMessage(human_msg)]

    def _validate_vulnerability_report(
        self, vuln_info: dict, attempt: int, max_retries: int
    ) -> bool:
        """Validate vulnerability report and determine if retry is needed"""
        if vuln_info.get("possibily_vulnerable", False) and not vuln_info.get(
            "vulnerable_line"
        ):
            if attempt < max_retries:
                logger.warning(
                    "Vulnerability found but no line specified in attempt "
                    f"{attempt + 1}. Retrying..."
                )
                return True
            else:
                logger.error(
                    "Failed to pinpoint vulnerability line after "
                    f"{max_retries} attempts"
                )
                vuln_info["possibily_vulnerable"] = False
                vuln_info["analysis_message"] += (
                    "\nNote: Potential vulnerability detected but could not be "
                    "pinpointed."
                )
        return False

    def _create_vulnerability_error_report(self) -> VulnerabilityReport:
        """Create error report for vulnerability analysis"""
        return VulnerabilityReport(
            analysis_message="Error: Failed to parse analysis output properly.",
            possibily_vulnerable=False,
            vulnerable_line_str="",
            vulnerable_line_info=None,
            required_file_paths=[],
            sanitizer_type="",
        )

    async def _collect_all_calls(self, node: FuncInfo) -> List[FuncInfo]:
        from mlla.agents.cgpa import (  # get_fn_search_results
            CGParserAgent,
            CGParserInputState,
        )
        from mlla.utils.call_extractor import get_all_calls

        """Collect all function calls in the current node"""
        if not node.func_body:
            return []

        _callees, from_file_path = get_all_calls(
            node.func_location.file_path, node.func_body
        )

        # Convert tree_sitter.Node to FuncInfo
        results: List[FuncInfo] = []
        # callees = {callee.text.decode("utf-8") for callee in _callees if callee.text}
        graph = CGParserAgent(self.gc, no_llm=True).compile()

        children_info: dict[str, FuncInfo] = {
            fn_info.func_location.func_name: fn_info for fn_info in node.children
        }

        tasks = []
        callee_info_list = []

        for callee in _callees:
            callee_start_row = (
                callee.range.start_point.row + 1
                if from_file_path
                else callee.range.start_point.row + node.func_location.start_line
            )
            callee_start_col = callee.range.start_point.column
            callee_end_row = (
                callee.range.end_point.row + 1
                if from_file_path
                else callee.range.end_point.row + node.func_location.start_line
            )
            if not callee.text:
                continue

            callee_name = callee.text.decode("utf-8")

            if callee_name == "":
                continue

            if callee_name in children_info:
                results.append(children_info[callee_name])
                continue

            try:
                task = asyncio.create_task(
                    graph.ainvoke(
                        CGParserInputState(
                            messages=[],
                            fn_name=callee_name,
                            fn_file_path=None,
                            caller_file_path=node.func_location.file_path,
                            caller_fn_body=node.func_body,
                            callsite_location=(
                                callee_start_row,
                                callee_start_col + 1,
                            ),
                            callsite_range=(
                                callee_start_row,
                                callee_end_row,
                            ),
                        ),
                        self.gc.graph_config,
                    )
                )
                tasks.append(task)
                callee_info_list.append(callee_name)
            except Exception as e:
                logger.warning(
                    f"Error collecting callees: {e}\n{callee_name} is out of codebase."
                )
                continue

        cgpa_states = await asyncio.gather(*tasks)

        for cgpa_state, callee_name in zip(cgpa_states, callee_info_list):
            code_dict = cgpa_state.get("code_dict", None)
            if not code_dict:
                logger.debug(
                    f"Function ({callee_name}) not found during path expansion:"
                    f" {cgpa_state}"
                )
                continue
            results.append(code_dict)
        return results

    async def _get_all_callees(
        self, path_list: List[FuncInfo]
    ) -> Dict[str, list[FuncInfo]]:
        """Expand the path to include more required functions"""
        expanded_path: Dict[str, List[FuncInfo]] = {}
        original_list = [node.func_location.func_name for node in path_list]

        for node in path_list:
            try:
                _callees = await self._collect_all_calls(node)
            except Exception as e:
                logger.error(f"Error collecting callees: {e}")
                continue

            callees = [
                callee
                for callee in _callees
                if callee.func_location.func_name not in original_list
            ]
            expanded_path[node.func_location.func_name] = callees
        return expanded_path

    def _expand_code_snippet(
        self, path_list: List[FuncInfo], additional_paths: Dict[str, List[FuncInfo]]
    ) -> Tuple[str, Dict[str, FuncInfo]]:
        """Expand the code snippet to include more required functions"""
        function_lut = {}
        snippets = []
        count_added = 1
        for node in path_list:
            if not node.func_body:
                continue
            snippets.append(
                f"<file_path>{node.func_location.file_path}</file_path>\n"
                f"<func_prototype_and_func_body>\n{node.func_body}\n"
                "</func_prototype_and_func_body>"
            )

            additionals = additional_paths.get(node.func_location.func_name, [])
            for node in additionals:
                if not node.func_body:
                    continue
                snippets.append(
                    f"<added_{count_added}>{node.func_location.func_name}"
                    f"</added_{count_added}>\n"
                    f"<func_prototype_and_func_body>\n{node.func_body}\n"
                    "</func_prototype_and_func_body>"
                )
                function_lut[f"added_{count_added}"] = node
                count_added += 1
        return "\n\n".join(snippets), function_lut

    def _expand_path_list(
        self,
        path_list: List[FuncInfo],
        additional_paths: Dict[str, List[FuncInfo]],
        required_nodes: List[FuncInfo],
    ) -> ExpandedPath:
        """Expand the path list to include more required functions"""
        expanded_path_list = []
        for node in path_list:
            callees = []
            if not node.func_body:
                continue
            for additional_node in additional_paths.get(
                node.func_location.func_name, []
            ):
                if additional_node in required_nodes:
                    callees.append(additional_node)
            expanded_path_list.append([node] + callees)
        return ExpandedPath(path_list=expanded_path_list)

    async def _prune_unnecessary_paths(self, path_list: List[FuncInfo]) -> ExpandedPath:
        path_and_callees = await self._get_all_callees(path_list)
        expanded_snippet, function_lut = self._expand_code_snippet(
            path_list, path_and_callees
        )

        # Ask LLM to select necessary functions for vulnerability detection
        prompt = PRUNE_UNNECESSARY_SYSTEM
        messages = [SystemMessage(prompt), HumanMessage(expanded_snippet)]

        try:
            # add_cache_control(messages[0])
            response = await self.llm_prune_path.ainvoke(messages)
            content = response[-1].content
        except Exception as e:
            logger.error(f"Error pruning paths: {e}")
            return ExpandedPath(path_list=[[path] for path in path_list])

        prompt = PRUNE_UNNECESSARY_FORMAT
        messages = [SystemMessage(prompt), HumanMessage(content)]
        try:
            response = await self.llm_lightweight_keyword_extracor.ainvoke(messages)
            parsed_tags = response[-1].keywords
        except Exception as e:
            logger.error(f"Error extracting tags: {e}")
            return ExpandedPath(path_list=[[path] for path in path_list])

        required_nodes = [
            function_lut[tag] for tag in parsed_tags if tag in function_lut
        ]
        expanded_path_list = self._expand_path_list(
            path_list, path_and_callees, required_nodes
        )

        return expanded_path_list

    def is_valid_sink(self, extracted_paths: ExtractedPath) -> bool:
        """Validate the path"""
        sink_func = extracted_paths.paths_to_sink[-1]

        if (
            not sink_func.sink_detector_report
            or not sink_func.sink_detector_report.is_vulnerable
        ):
            logger.error(
                f"Invalid sink function is given, but continue to analyze: {sink_func}"
            )
            return True

        if (
            sink_func.sink_detector_report.sink_line_number
            < sink_func.func_location.start_line
            or sink_func.sink_detector_report.sink_line_number
            > sink_func.func_location.end_line
        ):
            logger.warning(
                f"Skip the path because the sink line number is invalid: {sink_func}"
            )
            return False
        return True

    async def classify(
        self, state: BugCandDetectAgentOverallState
    ) -> BugCandDetectAgentOverallState:
        """Classify vulnerabilities and identify bug-inducing things"""
        logger.info(
            f"[BCDA|{state['extracted_paths'][0].create_tag()}] Starting vulnerability"
            " classification"
        )

        bits: List[BugInducingThing] = []

        analysis_tasks = []
        path_mapping: List[Tuple[Task[VulnerabilityReport], ExpandedPath]] = (
            []
        )  # Store mapping between tasks and paths

        expansion_tasks = []
        for extracted_path in state["extracted_paths"]:
            # if not self.is_valid_sink(extracted_path):
            #     continue

            path_list = extracted_path.paths_to_sink
            expansion_tasks.append(self._prune_unnecessary_paths(path_list))

        expanded_paths = await asyncio.gather(*expansion_tasks, return_exceptions=True)

        logger.info(
            f"[BCDA|{state['extracted_paths'][0].create_tag()}] Pruning unnecessary"
            " paths DONE"
        )

        for expanded_path, extracted_path in zip(
            expanded_paths, state["extracted_paths"]
        ):
            if isinstance(expanded_path, Exception):
                logger.error(f"Error in expanding path: {expanded_path}")
                import traceback

                tb_lines = traceback.format_exception(
                    type(expanded_path), expanded_path, expanded_path.__traceback__
                )
                logger.error("".join(tb_lines))
                default_path = ExpandedPath(
                    path_list=[[path] for path in extracted_path.paths_to_sink]
                )
                task = asyncio.create_task(
                    self._analyze_vulnerability(
                        expanded_path=default_path,
                        sanitizer_candidates=extracted_path.sanitizer_candidates,
                    )
                )
            else:
                task = asyncio.create_task(
                    self._analyze_vulnerability(
                        expanded_path=expanded_path,
                        # sink_line=extracted_path.sink_line,
                        # sink_line_number=extracted_path.paths_to_sink[-1].sink_detector_report.sink_line_number,
                        sanitizer_candidates=extracted_path.sanitizer_candidates,
                    )
                )
            analysis_tasks.append(task)
            # flatten_path_list = [node for group in path_list for node in group]
            path_mapping.append((task, expanded_path))

        # Wait for all analysis tasks to complete
        results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

        logger.info(
            f"[BCDA|{state['extracted_paths'][0].create_tag()}] Vulnerability"
            " analysis DONE"
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Vulnerability analysis failed: {result}")
                import traceback

                tb_lines = traceback.format_exception(
                    type(result), result, result.__traceback__
                )
                logger.error("".join(tb_lines))
                continue

        # Process results while maintaining the correct mapping
        harness_name = self.gc.cur_harness.name

        for task, expanded_path in path_mapping:
            if isinstance(task, Exception):
                logger.warning(f"Vulnerability analysis failed: {task}")
                continue
            vuln_info: VulnerabilityReport = await task

            if vuln_info.possibily_vulnerable and vuln_info.vulnerable_line_info:
                bit = await self._process_vulnerability(
                    harness_name, expanded_path, vuln_info
                )
                if bit:
                    bits.append(bit)
            elif vuln_info.possibily_vulnerable and not vuln_info.vulnerable_line_info:
                logger.error(
                    f"Could not pinpoint the specified vulnerable line {vuln_info}"
                )

        logger.info(
            f"[BCDA|{state['extracted_paths'][0].create_tag()}] Processing"
            " vulnerability DONE"
        )

        # Wait for all callback tasks to complete
        # if callback_tasks:
        #     callback_results = await asyncio.gather(*callback_tasks)
        #     bits.extend([result for result in callback_results if result])

        state["BITs"] = bits

        bit_str = ""
        for bit in bits:
            func_name = bit.func_location.func_name
            start_line = bit.func_location.start_line
            end_line = bit.func_location.start_line
            vuln_type = bit.analysis_message[0].sanitizer_type
            bit_str += (
                f"   - [{vuln_type}] {func_name}: line: {start_line} - {end_line}\n"
            )

        logger.info(
            f"[BCDA|{state['extracted_paths'][0].create_tag()}] Found"
            f" {len(bits)} bug-inducing things:\n{bit_str}"
        )

        return state

    def _filter_duplicate_bits(
        self, bits: List[BugInducingThing]
    ) -> List[BugInducingThing]:
        """Filter duplicate bug-inducing things"""
        unique_bits = {}
        for bit in bits:
            key = (
                bit.func_location.file_path,
                bit.func_location.func_name,
                bit.func_location.start_line,
                bit.func_location.end_line,
            )
            if key not in unique_bits:
                unique_bits[key] = bit
            else:
                if len(bit.analyzed_functions) > len(
                    unique_bits[key].analyzed_functions
                ):
                    unique_bits[key] = bit
                elif len(bit.analyzed_functions) == len(
                    unique_bits[key].analyzed_functions
                ):
                    if len(bit.key_conditions) + len(bit.should_be_taken_lines) > len(
                        unique_bits[key].key_conditions
                    ) + len(unique_bits[key].should_be_taken_lines):
                        unique_bits[key] = bit
        return list(unique_bits.values())

    def _divide_path_list(
        self, path_list: List[FuncInfo]
    ) -> List[Tuple[FuncInfo, FuncInfo]]:
        """Divide path list into groups of neighboring functions"""
        groups = []
        for i in range(len(path_list) - 1):
            groups.append((path_list[i], path_list[i + 1]))
        return groups

    async def extract_partial_conditions(
        self, code_prompt: str, current_node: List[FuncInfo], prev_node: List[FuncInfo]
    ) -> Tuple[List[LocationInfo], List[LocationInfo], str]:
        system_msg = EXTRACT_PARTIAL_CONDITION_SYSTEM
        human_msg = code_prompt

        messages = [SystemMessage(system_msg), HumanMessage(human_msg)]

        # TODO: If model evolves, two-step analyze-parse process
        # can be changed to end-to-end one-step parse process.
        try:
            response = await self.llm_partial_key_cond.ainvoke(messages)
        except Exception as e:
            logger.error(f"Error in partial key condition extraction: {e}")
            return [], [], ""

        analysis_message = response[-1]

        key_cond_messages = [
            SystemMessage(EXTRACT_PARTIAL_CONDITION_FORMAT),
            HumanMessage(
                f"{code_prompt}"
                f"\n\n<analysis_result>\n{analysis_message.content}\n</analysis_result>"
            ),
        ]

        def verify_line_exists(
            result: KeyConditionReport, remaining_retries: int
        ) -> Tuple[List[LocationInfo], List[LocationInfo]]:
            verified_key_conditions: List[LocationInfo] = []
            verified_should_be_taken_lines: List[LocationInfo] = []

            _key_condition_info: List[LineInfo] = result.key_conditions
            _should_be_taken_lines_info: List[LineInfo] = result.next_lines

            file_id = f"bcda_{id(messages)}"
            artifact_path = self.gc.workdir / "bcda_prompts"

            for line in _key_condition_info:
                key_condition = self.pinpoint_line(current_node + prev_node, line)
                if key_condition:
                    verified_key_conditions.append(key_condition)
                else:
                    if remaining_retries > 0:
                        raise Exception(
                            f"Key condition information is incorrect: {line}. Please"
                            " double check the function name, file path, and line"
                            " number."
                        )
                    else:
                        try:
                            artifact_path.mkdir(parents=True, exist_ok=True)
                            prompt_path = artifact_path / f"{file_id}.prompt"
                            store_artifact_files(prompt_path, prompts=messages)
                            result_path = artifact_path / f"{file_id}.result.prompt"
                            store_artifact_files(
                                result_path, prompts=[analysis_message]
                            )
                            key_cond_path = artifact_path / f"{file_id}.key_cond.prompt"
                            store_artifact_files(
                                key_cond_path, prompts=key_cond_messages
                            )
                            logger.error(f"[BCDA] Key condition not found: {line}")
                        except Exception as e:
                            logger.error(f"[BCDA] Key condition not found: {line}")
                            logger.error(
                                f"[BCDA] Error in key condition prompt storing:{e}"
                            )

            for line in _should_be_taken_lines_info:
                should_be_taken_line = self.pinpoint_line(
                    current_node + prev_node, line
                )
                if should_be_taken_line:
                    verified_should_be_taken_lines.append(should_be_taken_line)
                else:
                    if remaining_retries > 0:
                        raise Exception(
                            f"Should be taken line information is incorrect: {line}."
                            " Please double check the function name, file path, and"
                            " line number."
                        )
                    else:
                        try:
                            artifact_path.mkdir(parents=True, exist_ok=True)
                            prompt_path = artifact_path / f"{file_id}.prompt"
                            store_artifact_files(prompt_path, prompts=messages)
                            result_path = artifact_path / f"{file_id}.result.prompt"
                            store_artifact_files(
                                result_path, prompts=[analysis_message]
                            )
                            should_be_taken_line_path = (
                                artifact_path / f"{file_id}.should_be_taken_line.prompt"
                            )
                            store_artifact_files(
                                should_be_taken_line_path, prompts=key_cond_messages
                            )
                            logger.error(
                                f"[BCDA] Should be taken line not found: {line}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[BCDA] Should be taken line not found: {line}"
                            )
                            logger.error(
                                "[BCDA] Error in should be taken line prompt"
                                f" storing:{e}"
                            )
            return verified_key_conditions, verified_should_be_taken_lines

        key_conditions: List[LocationInfo] = []
        should_be_taken_lines: List[LocationInfo] = []
        try:
            # results = await self.llm_extract_key_cond.ainvoke(key_cond_messages)
            key_conditions, should_be_taken_lines = (
                await self.llm_extract_key_cond.aask_and_repeat_until(
                    verify_line_exists,
                    key_cond_messages,
                    ([], []),
                    max_retries=4,
                    try_with_error=True,
                    pass_retries_to_verifier=True,
                )
            )
        except Exception as e:
            logger.error(f"Error in key_condition extraction: {e}")
            return key_conditions, should_be_taken_lines, str(analysis_message.content)

        # result: KeyConditionReport = results[-1]

        return key_conditions, should_be_taken_lines, str(analysis_message.content)

    def dedup_location_info(
        self, location_info: List[LocationInfo]
    ) -> List[LocationInfo]:
        """Deduplicate location information"""
        seen = set()
        deduped = []
        for loc in location_info:
            key = (loc.file_path, loc.func_name, loc.start_line, loc.end_line)
            if key not in seen:
                seen.add(key)
                deduped.append(loc)
        return deduped

    async def _process_vulnerability(
        self,
        harness_name: str,
        expanded_path: ExpandedPath,
        vuln_info: VulnerabilityReport,
    ) -> BugInducingThing | None:
        """Process a detected vulnerability"""
        logger.info(
            f"[BCDA|{vuln_info.vulnerable_line_info}] Extracting partial conditions"
        )
        path_list = expanded_path.path_list

        tasks = []
        for i in range(len(path_list), 0, -1):
            current_node = [path_list[i][0]] if i < len(path_list) else []
            prev_node = path_list[i - 1]
            code_snippet = expanded_path.code_with_path(
                [prev_node, current_node], include_diff=False
            )
            call_flow = expanded_path.get_call_flow([prev_node, current_node])
            source_line = (
                "Source line: The entry point of"
                f" {prev_node[0].func_location.func_name}"
            )
            if current_node:
                target_line = (
                    "Target line: The entry point of"
                    f" {current_node[0].func_location.func_name}"
                )
            else:
                if not vuln_info.vulnerable_line_info:
                    logger.error(
                        f"Could not pinpoint the specified vulnerable line {vuln_info}"
                    )
                    continue
                target_line = (
                    f"Target line: {vuln_info.vulnerable_line_info.line_number}:"
                    f" {vuln_info.vulnerable_line_str}"
                )
            code_prompt = f"""Call flow:
{call_flow}

Code:
{code_snippet}

{source_line}
{target_line}"""

            # await self.extract_partial_conditions(code_prompt)
            tasks.append(
                self.extract_partial_conditions(code_prompt, current_node, prev_node)
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

        key_conditions: List[LocationInfo] = []
        should_be_taken_lines: List[LocationInfo] = []
        reports = []
        for i, result in zip(range(len(path_list), 0, -1), results):
            if isinstance(result, Exception):
                logger.error(f"Error in result: {result}")
                import traceback

                tb_lines = traceback.format_exception(
                    type(result), result, result.__traceback__
                )
                logger.error("".join(tb_lines))
                continue
            key_cond, next_lines, report = result
            key_conditions.extend(key_cond)
            should_be_taken_lines.extend(next_lines)
            reports.append(report)

        key_conditions = self.dedup_location_info(key_conditions)
        should_be_taken_lines = self.dedup_location_info(should_be_taken_lines)
        key_conditions_report = "\n".join(reports)

        # condition_lines = list(set(condition_lines))
        # taken_lines = list(set(taken_lines))
        logger.info(f"KEY CONDITIONS: {key_conditions}")
        logger.info(f"SHOULD BE TAKEN LINES: {should_be_taken_lines}")

        flattened_path_list = [node for group in path_list for node in group]
        result = self.pinpoint_line(
            flattened_path_list,
            vuln_info.vulnerable_line_info,
            vuln_info.vulnerable_line_str,
        )

        # _key_conditions = [
        #     self.pinpoint_line(flattened_path_list, line) for line in condition_lines
        # ]
        # _should_be_taken_lines = [
        #     self.pinpoint_line(flattened_path_list, line) for line in taken_lines
        # ]

        # key_conditions = [loc for loc in _key_conditions if loc is not None]
        # should_be_taken_lines = [
        #     loc for loc in _should_be_taken_lines if loc is not None
        # ]
        # logger.debug(
        #     f"key_conditions: {key_conditions}, condition_lines: {condition_lines}"
        # )

        if result:
            return self._create_bit(
                harness_name,
                path_list,
                vuln_info,
                result,
                key_conditions,
                should_be_taken_lines,
                key_conditions_report,
            )
        else:
            logger.error(
                f"Could not pinpoint the specified vulnerable line {vuln_info}"
            )
            return None

    def _determine_priority(self, path_list: List[List[FuncInfo]]) -> int:
        """Determine the priority of the path list"""
        for group in path_list:
            for func in group:
                if func.interest_info and func.interest_info.is_interesting:
                    return InterestPriority.CONTAIN_DIFF_FUNCTION
        return InterestPriority.NORMAL

    def _create_bit(
        self,
        harness_name: str,
        path_list: List[List[FuncInfo]],
        vuln_info: VulnerabilityReport,
        result: LocationInfo,
        key_conditions: List[LocationInfo],
        should_be_taken_lines: List[LocationInfo],
        key_conditions_report: str,
    ) -> BugInducingThing:
        """Create and store a bug-inducing thing"""
        vuln_func_info = path_list[-1][0]
        analysis_message = [
            AnalysisMessages(
                sink_detection=(
                    vuln_func_info.sink_detector_report.sink_analysis_message
                    if vuln_func_info.sink_detector_report
                    else ""
                ),
                vulnerability_classification=vuln_info.analysis_message,
                sanitizer_type=vuln_info.sanitizer_type,
                key_conditions_report=key_conditions_report,
            ),
        ]
        bit = BugInducingThing(
            harness_name=harness_name,
            func_location=result,
            key_conditions=key_conditions,
            should_be_taken_lines=should_be_taken_lines,
            analysis_message=analysis_message,
            analyzed_functions=[
                AnalyzedFunction(
                    func_location=f.func_location,
                    func_body=f.func_body,
                )
                for group in path_list
                for f in group
                if f.func_body
            ],
            priority=self._determine_priority(path_list),
        )

        return bit

    def preprocess(self, state):
        """Initialize the state for processing"""
        state["BITs"] = []
        return state

    def finalize(self, state):
        """Finalize the processing and return the state"""

        bits = state["BITs"]

        bits = self._filter_duplicate_bits(bits)

        state["BITs"] = bits

        bit_str = ""
        for bit in bits:
            func_name = bit.func_location.func_name
            start_line = bit.func_location.start_line
            end_line = bit.func_location.start_line
            vuln_type = bit.analysis_message[0].sanitizer_type
            bit_str += (
                f"   - [{vuln_type}] {func_name}: line: {start_line} - {end_line}\n"
            )

        logger.info(
            f"Finalizing with {len(state['BITs'])} bug-inducing things found:\n"
            f"{bit_str}"
        )

        dumping_state = {
            "BITs": [bit.to_dict() for bit in bits],
        }

        state_json = self.serialize(dumping_state)
        workdir_file = get_file_name(state, self.ret_file)
        try:
            with open(workdir_file, "a") as f:
                f.write(state_json + "\n")
                logger.info(f"Saved bug-inducing thing at {workdir_file}")
        except Exception as e:
            logger.error(f"Failed to save bit to {workdir_file}: {e}")

        # Optionally save to output directory
        if self.gc.BIT_OUTPUT_DIR:
            bit_name = self.gc.BIT_OUTPUT_DIR / f"{workdir_file.name}"
            try:
                with open(bit_name, "a") as f:
                    f.write(state_json + "\n")
                logger.debug(f"Saved bug-inducing thing: {bit_name}")
                done_path = bit_name.with_suffix(".done")
                done_path.touch()
            except Exception as e:
                logger.error(f"Failed to save bit to {bit_name}: {e}")

        # XXX: for debug
        # exit()
        set_done(self.gc, state["extracted_paths"])
        return state

    def deserialize(self, state, content: str) -> dict:
        if not self.prev_ret_file:
            logger.info("Deserialize failed: no previous results")
            return {"BITs": []}

        file_path = get_file_name(state, self.prev_ret_file)
        if not file_path.exists():
            logger.info("Deserialize failed: no previous results")
            return {"BITs": []}

        with open(file_path, "r") as f:
            prev_content = f.read()

        deserialized_state = deserialize_bcda(state, prev_content)
        return {"BITs": deserialized_state["BITs"]}

    def serialize(self, state) -> str:
        return json.dumps(state, indent=2)


def get_file_name(state, file_path: Path) -> Path:
    if not state or "extracted_paths" not in state:
        return file_path

    vul_fn = state["extracted_paths"][0].paths_to_sink[-1].func_location.func_name
    vul_type = state["extracted_paths"][0].sink_detector_report.sanitizer_candidates[0]
    hash = state["extracted_paths"][0].create_tag()[:8]
    postfix = f"{vul_fn}-{vul_type}-{hash}"

    file_prefix = "????-??-??_??-??-??"
    new_name = f"{file_path.stem[:len(file_prefix)]}-{postfix}{file_path.suffix}"
    return file_path.with_name(new_name)


def deserialize_bcda(state, content: str) -> BugCandDetectAgentOutputState:
    """Deserialize previous results from file"""
    logger.debug("Deserializing previous results")
    BITs = []

    state_dict = json.loads(content)

    for bit_dict in state_dict["BITs"]:
        try:
            bit = BugInducingThing.from_dict(bit_dict)
            BITs.append(bit)
            logger.debug(
                f"Deserialized BIT: {bit.func_location.func_name}@{bit.harness_name}"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Failed to deserialize bit: {bit_dict} due to {e}")
    logger.info(f"Deserialized {len(BITs)} bug-inducing things")
    return BugCandDetectAgentOutputState(BITs=BITs)
