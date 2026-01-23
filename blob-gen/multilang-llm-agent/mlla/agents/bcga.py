import json
from typing import Annotated, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.graph import MessagesState
from loguru import logger
from multilspy.multilspy_types import Location
from pydantic import BaseModel

from ..prompts.bcga import DIFF_FILTER_SYSTEM
from ..utils import normalize_func_name
from ..utils.agent import BCGA, BaseAgentTemplate
from ..utils.cg import CG, FuncInfo
from ..utils.cg.visitor import FuncGraph, SinkDetectVisitor
from ..utils.context import GlobalContext
from ..utils.diff_analyzer import FunctionDiff, accumulate_diffs
from ..utils.llm import LLM
from ..utils.state import merge_with_update


class DiffFilterOutput(BaseModel):
    is_vulnerable: bool
    vulnerable_functions: List[str]


class BackwardCallGraphAgentState(MessagesState):
    CGs: Annotated[dict[str, List[CG]], merge_with_update]


class BackwardCallGraphAgentOutputState(MessagesState):
    pass


class BackwardCallGraphOverallState(
    BackwardCallGraphAgentState, BackwardCallGraphAgentOutputState
):
    pass


def get_line_columns(
    file_path: str, func_name: str, start_line: int, end_line: int
) -> List[tuple[int, int]] | None:
    with open(file_path, "r") as f:
        lines = f.readlines()
    # check 10 lines after start_line
    lines = lines[start_line - 1 : end_line]
    res = []
    for i, line in enumerate(lines):
        if func_name + "(" in line:
            column = line.index(func_name)
            line_num = start_line + i
            res.append((line_num, column))

    return res


class BackwardCallGraphAgent(BaseAgentTemplate):
    diffs: dict[str, List[FunctionDiff]]

    def __init__(self, config: GlobalContext, diffs: dict[str, List[FunctionDiff]]):
        ret_dir = config.RESULT_DIR / BCGA
        super().__init__(
            config,
            ret_dir,
            input_state=BackwardCallGraphAgentState,
            output_state=BackwardCallGraphAgentOutputState,
            overall_state=BackwardCallGraphOverallState,
            step_mapper={},
        )

        self.builder.add_node("main", self.main)

        self.builder.add_edge("preprocess", "main")
        self.builder.add_edge("main", "finalize")

        self.diffs = diffs

    def preprocess(
        self, state: BackwardCallGraphAgentState
    ) -> BackwardCallGraphOverallState:
        return state

    def finalize(
        self, state: BackwardCallGraphOverallState
    ) -> BackwardCallGraphAgentOutputState:
        return state

    async def filter_diffs_by_vulnerability(
        self, orig_diffs: dict[str, List[FunctionDiff]]
    ) -> dict[str, List[FunctionDiff]]:
        max_retries = 3

        async def _call_llm(
            accumulated_diff: str, attempt: int
        ) -> Optional[DiffFilterOutput]:
            llm = LLM(model="gpt-4.1-mini", config=self.gc)
            response = await llm.ainvoke(
                [SystemMessage(DIFF_FILTER_SYSTEM), HumanMessage(accumulated_diff)]
            )

            output_parser = PydanticOutputParser(pydantic_object=DiffFilterOutput)
            try:
                output = output_parser.parse(response[-1].content)
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"Error parsing output: {response[-1].content}\n{e}")
                    return await _call_llm(accumulated_diff, attempt + 1)
                else:
                    logger.error(f"Error parsing output: {response[-1].content}\n{e}")
                    return None

            return output

        filtered_by_vulnerability_diffs = {}
        for file_name, diffs in orig_diffs.items():
            accumulated_diff = accumulate_diffs(diffs)
            if not accumulated_diff:
                continue

            output = await _call_llm(accumulated_diff, 0)
            if output and output.is_vulnerable:
                logger.info(f"vulnerable functions: {output.vulnerable_functions}")
                vulnerable_diffs = [
                    diff
                    for diff in diffs
                    if normalize_func_name(diff.func_name)
                    in output.vulnerable_functions
                ]
                filtered_by_vulnerability_diffs[file_name] = vulnerable_diffs

        logger.info(
            "# of files in filtered_by_vulnerability_diffs:"
            f" {len(filtered_by_vulnerability_diffs)}"
        )

        logger.info(
            "# of diffs in filtered_by_vulnerability_diffs:"
            f" {sum(len(diffs) for diffs in filtered_by_vulnerability_diffs.values())}"
        )

        return filtered_by_vulnerability_diffs

    async def filter_diffs_by_sink(
        self, diffs: List[FunctionDiff]
    ) -> List[FunctionDiff]:
        visited_nodes: dict[str, FuncInfo] = {}

        filtered_by_sink_diffs = []

        for diff in diffs:
            func_info = diff.to_func_info()

            graph = FuncGraph(func_info, visited_nodes)

            await graph.async_traverse(SinkDetectVisitor(self.gc))

            if (
                func_info.sink_detector_report
                and func_info.sink_detector_report.is_vulnerable
            ):
                logger.info(f"sink_detect_report: {func_info.sink_detector_report}")
                filtered_by_sink_diffs.append(diff)

        return filtered_by_sink_diffs

    async def main(
        self, state: BackwardCallGraphOverallState
    ) -> BackwardCallGraphOverallState:

        async def get_lsp_res(
            file_path: str, func_name: str, fn_start_line: int, fn_end_line: int
        ) -> List[Location]:
            fn_name = normalize_func_name(func_name)
            res = get_line_columns(file_path, fn_name, fn_start_line, fn_end_line)

            if res:
                for line_num, column in res:
                    try:
                        orig_lsp_res = await self.gc.lsp_server.request_references(
                            file_path, line_num, column
                        )
                    except Exception as e:
                        # if "Internal error" in str(e):
                        #     pass
                        # else:
                        logger.error(
                            "Error requesting references for"
                            f" {file_path}:{line_num}:{column}: {e}"
                        )
                        return []
                    lsp_res = list(
                        filter(lambda x: x.get("uri").startswith("file"), orig_lsp_res)
                    )
                    if lsp_res:
                        return lsp_res
                    else:
                        logger.warning(
                            f"no lsp_res for {file_path}:{line_num}:{column}"
                        )
                        logger.warning(f"orig_lsp_res: {orig_lsp_res}")
                        return orig_lsp_res
            else:
                logger.warning("get_line_columns returned None")
                logger.warning(
                    f"- {func_name}@ {file_path}:{fn_start_line}-{fn_end_line}"
                )
                logger.warning(f"fn_name: {fn_name}")
            return []

        filtered_by_vulnerability_diffs = await self.filter_diffs_by_vulnerability(
            self.diffs
        )
        filtered_by_ref_diffs = []

        for file_name, diffs in filtered_by_vulnerability_diffs.items():
            logger.info(f"# of diffs in {file_name}: {len(diffs)}")
            for diff in diffs:
                if diff.fn_start_line and diff.fn_end_line:
                    lsp_res = await get_lsp_res(
                        diff.file_path,
                        diff.func_name,
                        diff.fn_start_line,
                        diff.fn_end_line,
                    )
                    if lsp_res:
                        filtered_by_ref_diffs.append(diff)

        logger.info(f"# of filtered_by_ref_diffs: {len(filtered_by_ref_diffs)}")

        # filtered_by_sink_diffs = await
        # self.filter_diffs_by_sink(filtered_by_ref_diffs)

        # logger.info(f"# of filtered_by_sink_diffs: {len(filtered_by_sink_diffs)}")

        for diff in filtered_by_ref_diffs:
            logger.info(f"diff.func_name: {diff.func_name}")
            logger.info(f"diff.file_path: {diff.file_path}")

        return state

    def serialize(self, state) -> str:
        return json.dumps(state, indent=2)

    def deserialize(self, state, content: str) -> dict:
        return json.loads(content)
