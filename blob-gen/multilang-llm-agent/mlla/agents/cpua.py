import asyncio
import json
import os
from pathlib import Path
from queue import Queue

# from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.tools.base import BaseTool
from langgraph.graph import MessagesState, add_messages
from loguru import logger
from pydantic import BaseModel
from typing_extensions import Annotated, List, Optional, Set

from mlla.utils.execute_llm_code import collect_code_block

from ..modules.class_understanding import extract_external_methods
from ..prompts.cpua import (
    CPUA_ERROR,
    UNDERSTAND_HARNESSES_FORMAT,
    UNDERSTAND_HARNESSES_HUMAN,
    UNDERSTAND_HARNESSES_SYSTEM,
    UNDERSTAND_REFLECTION_SYSTEM,
)
from ..utils import instrument_line
from ..utils.agent import CPUA, TOOL_MODEL, BaseAgentTemplate
from ..utils.cg import CG, FuncInfo, LocationInfo
from ..utils.cgparser import validate_functions  # CGParser,
from ..utils.context import GlobalContext
from ..utils.cp import sCP_Harness
from ..utils.diff_analyzer import FunctionDiff
from ..utils.llm import LLM
from ..utils.state import merge_with_update
from .bcga import BackwardCallGraphAgent, BackwardCallGraphAgentState
from .cgpa import CGParserAgent, CGParserInputState
from .mcga import MakeCallGraphAgent


class APIRes(BaseModel):
    entry_to_fn_list: List[str]
    tainted_args: List[int]
    line: int | None = None
    col: int | None = None
    function_path: str | None = None
    priority: int = 100


class CILLMAPIRes(BaseModel):
    func_name: str
    start_line: int
    end_line: int


class CIAPIRes(BaseModel):
    func_name: str
    file_path: str
    start_line: int
    end_line: int
    func_body: str
    tainted_args: List[int]


def ci_api_res_to_func_info(ci_api_res: CIAPIRes) -> FuncInfo:
    return FuncInfo(
        func_location=LocationInfo(
            file_path=ci_api_res.file_path,
            start_line=ci_api_res.start_line,
            end_line=ci_api_res.end_line,
            func_name=ci_api_res.func_name,
        ),
        func_body=ci_api_res.func_body,
        tainted_args=ci_api_res.tainted_args,
    )


def is_c_header_file(file_path: str) -> bool:
    return (
        file_path.endswith(".h")
        or file_path.endswith(".hpp")
        or file_path.endswith(".hh")
        or file_path.endswith(".hxx")
    )


async def make_new_root_node(
    entry_to_fn_list: list[str],
    orig_root_node: FuncInfo,
    harness_path: str,
    gc: GlobalContext,
) -> FuncInfo:
    cgpa = CGParserAgent(gc)
    cgpa_graph = cgpa.compile()

    if len(entry_to_fn_list) == 1:
        if entry_to_fn_list[0] == orig_root_node.func_location.func_name:
            return orig_root_node
        else:
            logger.warning(
                f"Entry point function {entry_to_fn_list[0]} is not the root"
                f" function {orig_root_node.func_location.func_name}."
            )
            cgpa_state = await cgpa_graph.ainvoke(
                CGParserInputState(
                    fn_name=entry_to_fn_list[0],
                    caller_file_path=harness_path,
                ),
            )
            new_fn_info = cgpa_state["code_dict"]
            new_fn_info.children = orig_root_node.children
            return new_fn_info
    else:
        cgpa_state = await cgpa_graph.ainvoke(
            CGParserInputState(
                fn_name=entry_to_fn_list[0],
                fn_file_path=harness_path,
            ),
        )
        new_fn_info = cgpa_state["code_dict"]
        new_fn_info.children = [
            await make_new_root_node(
                entry_to_fn_list[1:], orig_root_node, harness_path, gc
            )
        ]
        return new_fn_info


async def build_cgs_from_entry_points(
    cgs: dict[str, list[CG]],
    entry_to_fn_list_dict: dict[str, List[str]],
    gc: GlobalContext,
) -> dict[str, list[CG]]:
    replaced_cgs = {}
    cur_harness = gc.cur_harness
    _cgs = cgs[cur_harness.name]
    for cg in _cgs:
        if cg.name in entry_to_fn_list_dict:
            replaced_cgs[cg.name] = await make_new_root_node(
                entry_to_fn_list_dict[cg.name],
                cg.root_node,
                cur_harness.src_path.as_posix(),
                gc,
            )
    new_cgs = []
    for fn_name, cg_root_node in replaced_cgs.items():
        for cg in _cgs:
            if cg.name == fn_name:
                new_cgs.append(
                    CG(
                        name=fn_name,
                        path=cg.path,
                        root_node=cg_root_node,
                    )
                )
                break
    return {cur_harness.name: new_cgs}


# Input state
class CPUnderstandAgentState(MessagesState):
    # Possible sink functions from Sanitizers
    sink_functions: Annotated[List[str], merge_with_update]


# Output state
class CPUnderstandAgentOutputState(MessagesState):
    # TODO: dynamic output state
    # interesting path
    CGs: Annotated[dict[str, List[CG]], merge_with_update]
    input_sources: Annotated[List[str], merge_with_update]
    vuln_sink_functions: Annotated[List[str], merge_with_update]


# Intermediate state
class CPUnderstandOverallState(CPUnderstandAgentState, CPUnderstandAgentOutputState):
    step: Annotated[int, merge_with_update]
    extension_list: Annotated[List[str], merge_with_update]
    api_dict: Annotated[dict[str, APIRes], merge_with_update]  # {fn_name: APIRes, ...}
    ci_functions: Annotated[
        dict[str, tuple[CIAPIRes, FuncInfo]], merge_with_update
    ]  # {fn_name: CIAPIRes, ...}
    reflection_api_dict: Annotated[
        dict[str, APIRes], merge_with_update
    ]  # {fn_name: APIRes, ...}
    resolved: Annotated[dict[int, bool], merge_with_update]


def deserialize_cpua(content: str) -> CPUnderstandAgentOutputState:
    ret = json.loads(content)
    cgs_dict = {
        harness_name: [CG.model_validate(cg) for cg in cgs]
        for harness_name, cgs in ret["CGs"].items()
    }
    return CPUnderstandAgentOutputState(
        CGs=cgs_dict,
        input_sources=ret["input_sources"],
        vuln_sink_functions=ret["vuln_sink_functions"],
    )


class CPUnderstandAgent(BaseAgentTemplate):
    llm: LLM
    tools: list[BaseTool]
    get_file_path_verifier_cnt: int = 0

    def __init__(self, config: GlobalContext):
        ret_dir = config.RESULT_DIR / CPUA
        super().__init__(
            config,
            ret_dir,
            CPUnderstandAgentState,
            CPUnderstandAgentOutputState,
            CPUnderstandOverallState,
            step_mapper={
                1: "understand_harnesses",
                2: "understand_reflection",
                3: "get_file_path",
                4: "mcga",
            },
            llm_with_tools=os.getenv("CPUA_MODEL", "o4-mini"),
        )

        self.builder.add_node("understand_harnesses", self.understand_harnesses)
        self.builder.add_node("understand_reflection", self.understand_reflection)
        self.builder.add_node("get_file_path", self.get_file_path)
        self.builder.add_node("mcga", self.call_mcga)
        self.builder.add_node("parse_cgs_step", self.parse_cgs_step)
        self.builder.add_node(
            "collect_cg_external_calls", self.collect_cg_external_calls
        )
        self.builder.add_node("check_diff", self.check_diff)

        self.builder.add_edge("preprocess", "understand_harnesses")
        self.builder.add_conditional_edges(
            "understand_harnesses",
            CPUnderstandAgent.switch_u_reflection,
            ["understand_reflection", TOOL_MODEL],
        )

        self.builder.add_conditional_edges(
            "understand_reflection",
            CPUnderstandAgent.switch_get_file_path,
            ["get_file_path", TOOL_MODEL],
        )

        # self.builder.add_conditional_edges(
        #     "get_file_path", CPUnderstandAgent.swtich_make_cgnode, ["mcga",
        #     TOOL_MODEL]
        # )
        self.builder.add_edge("get_file_path", "mcga")
        self.builder.add_edge("mcga", "parse_cgs_step")
        self.builder.add_edge("parse_cgs_step", "check_diff")
        self.builder.add_edge("check_diff", "collect_cg_external_calls")
        self.builder.add_edge("collect_cg_external_calls", "finalize")

        self.get_ci_api_res_llm = LLM(
            model="gemini-2.5-pro",
            config=config,
            output_format=CILLMAPIRes,
        )

    def pre_mcga(
        self, ci_functions: dict[str, tuple[CIAPIRes, FuncInfo]]
    ) -> Set[
        tuple[str, str, str, str, tuple[int, int], FuncInfo]
    ]:  # (func_name, file_path, func_body, tainted_args, (start_line,
        # end_line), code_dict)

        functions_set: Set[tuple[str, str, str, str, tuple[int, int], FuncInfo]] = set()

        for func_name, (ci_res, code_dict) in ci_functions.items():
            functions_set.add(
                (
                    func_name,
                    ci_res.file_path,
                    ci_res.func_body,
                    ",".join(map(str, ci_res.tainted_args)),
                    (ci_res.start_line, ci_res.end_line),
                    code_dict,
                )
            )

        return functions_set

    async def call_mcga(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        functions_set: Set[tuple[str, str, str, str, tuple[int, int], FuncInfo]] = (
            self.pre_mcga(state["ci_functions"])
        )
        cgs: dict[str, list[CG]] = {}
        harness_name = self.gc.cur_harness.name

        def _callback(mcga_state) -> None:
            cg_root_node: FuncInfo = mcga_state["cg_root_node"]
            if harness_name not in cgs:
                cgs[harness_name] = []

            if cg_root_node:
                fn_name = cg_root_node.func_location.func_name
                cg = CG(
                    name=fn_name,
                    path=cg_root_node.func_location.file_path,
                    root_node=cg_root_node,
                )
                cgs[harness_name].append(cg)

        def _handle_task_result(task):
            try:
                result = task.result()
                _callback(result)
            except Exception as e:
                logger.error(f"Error in result: {e}")
                import traceback

                tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
                logger.error("".join(tb_lines))

        target_fns = []
        api_dict = state["api_dict"]
        priority_queue: asyncio.PriorityQueue[tuple[int, int, int, asyncio.Future]] = (
            asyncio.PriorityQueue()
        )

        for idx, target_fn in enumerate(functions_set):
            real_target_fn = (
                target_fn[0],
                target_fn[1],
                target_fn[2],
                list(map(int, filter(lambda x: x.isdigit(), target_fn[3].split(",")))),
                target_fn[4],
            )
            entry_to_fn_list = api_dict[target_fn[0]].entry_to_fn_list
            target_fns.append((real_target_fn, entry_to_fn_list))
            MCGA = MakeCallGraphAgent(
                real_target_fn,
                self.gc,
                {},
                priority_queue=priority_queue,
                parent_fn=None,
                current_fn_info=target_fn[5],
            )
            graph = MCGA.compile()
            mcga_state = graph.ainvoke(state, self.gc.graph_config)

            task = asyncio.ensure_future(mcga_state)
            task.add_done_callback(_handle_task_result)
            # all_mcga_tasks.append(task)

            priority_queue.put_nowait((api_dict[target_fn[0]].priority, -1, idx, task))

        self.gc.set_cpua_target_fns(target_fns)
        await MakeCallGraphAgent.set_start_time()

        async def _run_tasks(
            queue: asyncio.PriorityQueue,
            functions_set: Set[tuple[str, str, str, str, tuple[int, int], FuncInfo]],
        ):
            tasks = []
            call_mcga_cnt = 0
            call_mcga_cnt_limit = len(functions_set)
            wait_cnt = 0
            timeout_val = 36
            if self.gc.in_ci:
                timeout_val = 18
            while True:
                try:
                    task = (await asyncio.wait_for(queue.get(), timeout=timeout_val))[
                        -1
                    ]
                    wait_cnt = 0
                except asyncio.TimeoutError:
                    wait_cnt += 1
                    if wait_cnt > 10:
                        logger.error(f"🔴 Timeout ({10}) reached")
                        break
                    continue
                if task is None:
                    call_mcga_cnt += 1
                    logger.info(
                        f"[CPUA] Call MCGA {call_mcga_cnt} /"
                        f" {call_mcga_cnt_limit} done."
                    )
                else:
                    tasks.append(task)
                if call_mcga_cnt == call_mcga_cnt_limit:
                    # sentinel
                    logger.info("[CPUA] All tasks done. Exiting...")
                    break

            results = []
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                pass

            for result in results:
                if isinstance(result, Exception):
                    import traceback

                    tb_lines = traceback.format_exception(
                        type(result), result, result.__traceback__
                    )
                    logger.error("".join(tb_lines))

        # try:
        #     grace_period = 300
        #     if self.gc.in_ci:
        #         grace_period = 10
        #     await asyncio.wait_for(
        #         _run_tasks(priority_queue, functions_set),
        #         timeout=MCGA.MAX_TIMEOUT + grace_period,  # 5 mins for grace period
        #     )
        # except asyncio.TimeoutError:
        #     logger.error(f"🔴 Timeout ({MCGA.MAX_TIMEOUT + grace_period}) reached")

        await _run_tasks(priority_queue, functions_set),

        return CPUnderstandOverallState(
            messages=[],
            CGs=cgs,
            input_sources=state["input_sources"],
            vuln_sink_functions=state["vuln_sink_functions"],
            step=state["step"],
            extension_list=state["extension_list"],
            api_dict=state["api_dict"],
            reflection_api_dict=state["reflection_api_dict"],
            ci_functions=state["ci_functions"],
        )

    @staticmethod
    def switch_u_reflection(
        state: CPUnderstandOverallState,
    ) -> str:
        if len(state["api_dict"]) != 0:
            return "understand_reflection"
        return TOOL_MODEL

    @staticmethod
    def switch_get_file_path(
        state: CPUnderstandOverallState,
    ) -> str:
        if len(state["reflection_api_dict"]) != 0 or len(state["messages"]) == 0:
            return "get_file_path"
        return TOOL_MODEL

    # @staticmethod
    # def swtich_make_cgnode(
    #     state: CPUnderstandOverallState,
    # ) -> str:
    #     if len(state["ci_functions"]) != 0:
    #         return "mcga"
    #     return TOOL_MODEL

    def serialize(self, state) -> str:
        ret_dict = {
            "CGs": {
                harness_name: [cg.model_dump() for cg in cgs]
                for harness_name, cgs in state["CGs"].items()
            },
            "input_sources": state["input_sources"],
            "vuln_sink_functions": state["vuln_sink_functions"],
        }

        return json.dumps(ret_dict, indent=2)

    def deserialize(self, state, content: str) -> dict:
        loaded_state = deserialize_cpua(content)

        queue: Queue | None = self.gc.candidate_queue
        if queue:
            cgs: dict[str, list[CG]] = loaded_state["CGs"]
            queue.put({"CGs": cgs})
            logger.info(f"[CPUA] Put {len(cgs)} CGs into candidate queue completed.")

        return loaded_state

    def preprocess(self, state: CPUnderstandAgentState) -> CPUnderstandOverallState:

        new_state = CPUnderstandOverallState(
            sink_functions=state["sink_functions"],
            messages=[],
            extension_list=[],
            ci_functions={},
            CGs={},
            input_sources=[],
            vuln_sink_functions=[],
            step=0,
            api_dict={},
            reflection_api_dict={},
            resolved={},
        )

        return new_state

    async def _understand_harnesses_verifier(
        self, response, is_reflection: bool = False
    ) -> dict[str, APIRes]:
        cur_harness: sCP_Harness = self.gc.cur_harness
        content = response.content.strip()

        emsgs = []

        parser = JsonOutputParser()

        try:
            json_contents = collect_code_block(content, "json")
            if not json_contents:
                json_contents = [content]
            api_dict = parser.invoke(json_contents[0])
        except Exception as e:
            raise Exception(f"response content is not valid json: {e}")

        for fn_name, api_res_dict in api_dict.items():
            # logger.debug(api_res_dict)
            entry_to_fn_list = api_res_dict["entry_to_function"]
            if not entry_to_fn_list:
                emsgs.append(
                    f"The list for entry function to {fn_name} is empty."
                    " Something is wrong."
                )

        # {fn_name: APIRes, ...}
        new_api_dict = {}

        for api_name, api_res_dict in api_dict.items():
            entry_to_fn_list = api_res_dict["entry_to_function"]

            if entry_to_fn_list[-1] != api_name:
                entry_to_fn_list.append(api_name)

            positions, _emsgs = await validate_functions(
                entry_to_fn_list,
                cur_harness.src_path.as_posix(),
                api_name,
                self.gc,
                self.gc.cp.cp_src_path.as_posix(),
                is_reflection=is_reflection,
            )

            line = api_res_dict["callsite_location"][0]
            col = api_res_dict["callsite_location"][1]
            function_path = api_res_dict["function_path"]

            if function_path and not Path(function_path).exists():
                function_path = None

            if _emsgs:
                emsgs.extend(_emsgs)
                emsgs.append(
                    f"Note that the harness file is {cur_harness.src_path.as_posix()}"
                )
            else:
                new_api_dict[api_name] = APIRes(
                    entry_to_fn_list=entry_to_fn_list,
                    tainted_args=api_res_dict["tainted_args"],
                    line=line,
                    col=col,
                    function_path=function_path,
                    priority=int(api_res_dict["priority"]),
                )

        if emsgs:
            error_msg = "\n".join(emsgs)
            raise Exception(error_msg)

        return new_api_dict

    def get_harness_info(self) -> list[str]:
        harness_info = []
        for _, harness in self.gc.cp.harnesses.items():
            if not harness.src_path.exists():
                continue
            with harness.src_path.open("r") as file:
                code = file.read()
                code, _ = instrument_line(code, 1)
            harness_info.append(
                UNDERSTAND_HARNESSES_FORMAT.format(
                    name=harness.name,
                    path=harness.src_path,
                    code=code,
                )
            )
        return harness_info

    async def understand_harnesses(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        harnesses: dict[str, sCP_Harness] = {}

        for _, harness in self.gc.cp.harnesses.items():
            if not harness.src_path.exists():
                continue
            harnesses[harness.name] = harness

        if len(harnesses) != 1:
            logger.error(f"Number of harnesses is not 1: {len(harnesses)}")

        if state["step"] == 1:
            last_message = state["messages"][-1]
            try:
                api_dict = await self._understand_harnesses_verifier(last_message)
            except Exception as e:
                msg = CPUA_ERROR.format(error=e)
                state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
                return state

            state["api_dict"] = api_dict
            # state["api_dict"] = {"getPage": api_dict["getPage"]}
            state["messages"] = [RemoveMessage(id=m.id) for m in state["messages"]]
            return state

        # Build harness information
        harness_info = self.get_harness_info()

        cp_name = self.gc.cp.name.split("/")[-1]
        cp_path = self.gc.cp.cp_src_path.as_posix()
        cur_harness = self.gc.cur_harness

        messages = [
            SystemMessage(
                UNDERSTAND_HARNESSES_SYSTEM.format(
                    cp_name=cp_name,
                    cp_path=cp_path,
                    harness_path=cur_harness.src_path.as_posix(),
                )
            ),
            HumanMessage(
                UNDERSTAND_HARNESSES_HUMAN.format(
                    harness_info="\n\n".join(harness_info)
                )
            ),
        ]

        # # FOR DEBUG
        # from mlla.utils.artifact_storage import store_artifact_files

        # harness_name = self.gc.target_harness
        # base_path = Path(f"/mlla/cpua_{harness_name}")
        # this_path = base_path / f"cpua_{id(messages)}"
        # this_path.parent.mkdir(parents=True, exist_ok=True)
        # store_artifact_files(
        #     base_path=this_path,
        #     prompts=messages,
        # )
        # logger.success(f"logging files to: {this_path}")

        state["messages"] = messages
        state["step"] = 1

        return state

    async def _understand_reflection_verifier(self, response) -> dict[str, APIRes]:
        # TODO: Implement reflection verifier
        return await self._understand_harnesses_verifier(response, is_reflection=True)

    async def understand_reflection(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        if state["step"] == 2:
            last_message = state["messages"][-1]
            try:
                reflection_api_dict = await self._understand_reflection_verifier(
                    last_message
                )
            except Exception as e:
                logger.error(f"Error: {e}")

                msg = CPUA_ERROR.format(error=e)
                state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
                return state

            state["reflection_api_dict"] = reflection_api_dict
            state["api_dict"].update(reflection_api_dict)
            state["messages"] = [RemoveMessage(id=m.id) for m in state["messages"]]
            return state

        harness_info = self.get_harness_info()
        cp_name = self.gc.cp.name.split("/")[-1]
        cp_path = self.gc.cp.cp_src_path.as_posix()

        messages = [
            HumanMessage(
                UNDERSTAND_REFLECTION_SYSTEM.format(
                    cp_name=cp_name, project_dir=cp_path
                )
            ),
            HumanMessage(
                UNDERSTAND_HARNESSES_HUMAN.format(
                    harness_info="\n\n".join(harness_info)
                )
            ),
        ]

        state["messages"] = messages
        state["step"] = 2

        return state

    def _get_file_path_verifier(
        self, response, harnesses: dict[str, sCP_Harness]
    ) -> dict[str, List[tuple[str, str]]]:
        content = response.content.strip()
        logger.debug(content)

        _funcions_per_harness_dict = {}
        emsgs = []

        parser = JsonOutputParser()

        try:
            harness_dict = parser.invoke(content)
        except Exception as e:
            raise Exception(f"response content is not valid json: {e}")

        all_empty = all([not v for v in harness_dict.values()])

        if all_empty:
            raise Exception(
                "All list of functions and file paths are empty."
                + " Provide at least one."
            )

        for harness_name, f_list in harness_dict.items():
            for fn_name, file_path in f_list:
                if not fn_name:
                    emsgs.append(
                        "The function name is empty."
                        + " Please provide a valid function name."
                    )
                if not file_path:
                    emsgs.append(
                        "The file path is empty." + " Please provide a valid file path."
                    )
                elif not Path(file_path).exists():
                    emsgs.append(f"File path {file_path} does not exist.")
                elif harnesses.get(harness_name) and harnesses[
                    harness_name
                ].src_path == Path(file_path):
                    emsgs.append(
                        f"`{fn_name}` is defined in the harness file:"
                        + f"`{file_path}`. Do not include function"
                        + " defined in the harness file as a target function."
                    )

            if emsgs:
                error_msg = "\n".join(emsgs)
                raise ValueError(error_msg)

            _funcions_per_harness_dict[harness_name] = f_list

        return _funcions_per_harness_dict

    def _get_ciapi_res(self, fn_name: str, api_res: APIRes) -> Optional[CIAPIRes]:
        if not api_res.function_path or not Path(api_res.function_path).exists():
            return None

        with open(api_res.function_path, "r") as file:
            code = file.read()
        instrumented_code, _ = instrument_line(code, 1)
        code_lines = code.split("\n")
        already_failed = False

        def _verifier(response) -> CILLMAPIRes:
            nonlocal already_failed

            if isinstance(response, CILLMAPIRes):
                if not already_failed:
                    if fn_name not in "".join(
                        code_lines[response.start_line - 3 : response.start_line + 3]
                    ):
                        already_failed = True
                        raise Exception(
                            f"The function name {fn_name} is not found near"
                            f" the start line {response.start_line}."
                        )

                return response

            content = response.content.strip()
            try:
                return CILLMAPIRes.model_validate_json(content)
            except Exception as e:
                raise Exception(f"response content is not valid CIAPIRes: {e}")

        res = self.get_ci_api_res_llm.ask_and_repeat_until(
            verifier=_verifier,
            messages=[
                SystemMessage("Make CILLMAPIRes for the following function."),
                HumanMessage(
                    f"function_name: {fn_name}\n<code>\n{instrumented_code}</code>\n"
                ),
            ],
            default=None,
        )

        if res:
            func_body = code_lines[res.start_line - 1 : res.end_line]
            return CIAPIRes(
                func_name=fn_name,
                file_path=api_res.function_path,
                start_line=res.start_line,
                end_line=res.end_line,
                func_body="\n".join(func_body),
                tainted_args=api_res.tainted_args,
            )

        return None

    async def get_file_path(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        harness = self.gc.cur_harness
        code_dicts: dict[str, FuncInfo] = {}
        api_dict = state["api_dict"].copy()
        del_api_dict = []
        cg_parser_agent = CGParserAgent(self.gc)
        graph = cg_parser_agent.compile()

        for fn_name, api_res in api_dict.items():
            if api_res.line and api_res.col:
                callsite_location = (api_res.line, api_res.col)
            else:
                callsite_location = None

            cg_parser_state = await graph.ainvoke(
                CGParserInputState(
                    fn_name=fn_name,
                    fn_file_path=api_res.function_path,
                    caller_file_path=harness.src_path.as_posix(),
                    caller_fn_body=None,
                    callsite_location=callsite_location,
                ),
            )
            code_dict = cg_parser_state["code_dict"]
            if code_dict:
                if self.gc.cp.language == "c" or self.gc.cp.language == "cpp":
                    if not api_res.function_path:
                        code_dicts[fn_name] = code_dict
                        del_api_dict.append(fn_name)
                    elif code_dict.func_location.file_path == api_res.function_path:
                        code_dicts[fn_name] = code_dict
                        del_api_dict.append(fn_name)
                    elif is_c_header_file(
                        code_dict.func_location.file_path
                    ) and not is_c_header_file(api_res.function_path):
                        logger.warning(
                            f"code_dict: {code_dict.func_location.file_path}"
                        )
                        logger.warning(f"api_res: {api_res.function_path}")
                        logger.warning(f"fn_name: {fn_name}")
                    else:
                        code_dicts[fn_name] = code_dict
                        del_api_dict.append(fn_name)
                else:
                    code_dicts[fn_name] = code_dict
                    del_api_dict.append(fn_name)
            else:
                logger.warning(f"Failed to get code dict for {fn_name}")
                logger.warning(f"fn_file_path: {api_res.function_path}")
                logger.warning(f"caller_location: {callsite_location}")

        for fn_name in del_api_dict:
            del api_dict[fn_name]

        new_state = state.copy()

        ci_functions = {}
        for fn_name, api_res in api_dict.items():
            logger.info(
                f"Looking for CIAPIRes for {fn_name} at {api_res.function_path}"
            )
            ci_api_res = self._get_ciapi_res(fn_name, api_res)
            if ci_api_res:
                ci_functions[fn_name] = (
                    ci_api_res,
                    ci_api_res_to_func_info(ci_api_res),
                )

        for fn_name, code_dict in code_dicts.items():
            ci_functions[fn_name] = (
                CIAPIRes(
                    func_name=fn_name,
                    file_path=code_dict.func_location.file_path,
                    start_line=code_dict.func_location.start_line,
                    end_line=code_dict.func_location.end_line,
                    func_body=code_dict.func_body,
                    tainted_args=state["api_dict"][fn_name].tainted_args,
                ),
                code_dict,
            )

        logger.info(f"Q. {self.gc.cp.name}'s interesting functions and file paths?")
        for fn_name, (ci_res, code_dict) in ci_functions.items():
            logger.info(f"- {fn_name} -> {ci_res.file_path}")

        new_state["ci_functions"] = ci_functions
        new_state["step"] = 3

        return new_state

    async def parse_cgs_step(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        cgs = state["CGs"]
        api_dict = state["api_dict"]

        logger.info(f"# of CGs: {sum(len(cgs) for cgs in cgs.values())}")

        entry_to_fn_list_dict = {}
        for fn_name, api_res in api_dict.items():
            entry_to_fn_list_dict[fn_name] = api_res.entry_to_fn_list

        cgs = await build_cgs_from_entry_points(cgs, entry_to_fn_list_dict, self.gc)

        state["CGs"] = cgs

        logger.info(f"Final # of CGs: {sum(len(cgs) for cgs in cgs.values())}")

        return state

    async def collect_cg_external_calls(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        cgs = state["CGs"]

        def _traverse(fi: FuncInfo):
            ret = [fi]

            if fi.children:
                for child in fi.children:
                    ret.extend(_traverse(child))

            return ret

        for harness_name in cgs:
            called_functions = []

            for cg in cgs[harness_name]:
                called_functions.extend(_traverse(cg.root_node))

            for cg in cgs[harness_name]:
                methods = extract_external_methods(
                    cg, called_functions, self.gc.cp.language
                )

                # dedup in case multiple CGs contained the same function
                cg.called_external_methods = list(
                    {f.func_location.func_name: f for f in methods}.values()
                )

        return state

    async def check_diff(
        self, state: CPUnderstandOverallState
    ) -> CPUnderstandOverallState:
        diffs_dict = self.gc.function_diffs
        diffs_num = sum(len(diffs) for diffs in diffs_dict.values())
        logger.info(f"# of diffs: {diffs_num}")
        if diffs_num == 0:
            logger.warning("No diffs found, skipping backward call graph generation")
            return state

        if not self.gc.lsp_server:
            logger.warning(
                "LSP server is not available, skipping backward call graph generation"
            )

        not_included_diffs: dict[str, list[FunctionDiff]] = {}
        for fn_name, diffs in diffs_dict.items():
            for diff in diffs:
                if diff.cg_included:
                    logger.info(f"{fn_name} is included in the CG")
                    logger.info(f"diff: {diff.diff}")
                    continue
                if fn_name not in not_included_diffs:
                    not_included_diffs[fn_name] = []
                not_included_diffs[fn_name].append(diff)

        logger.info(
            "# of not included diffs:"
            f" {sum(len(diffs) for diffs in not_included_diffs.values())}"
        )

        # TODO: after applying Joern
        logger.warning("TODO: after applying Joern")
        return state

        BCGA = BackwardCallGraphAgent(self.gc, not_included_diffs)
        cg_graph = BCGA.compile()
        await cg_graph.ainvoke(
            BackwardCallGraphAgentState(CGs=state["CGs"]), self.gc.graph_config
        )

        return state

    def finalize(self, state: CPUnderstandOverallState) -> CPUnderstandAgentOutputState:
        logger.info(f"Finalize CPUA with {len(state['CGs'])} CGs")

        if CPUA not in self.gc.load_agent_names:
            ret_json = self.serialize(state)

            with self.ret_file.open("w") as file:
                file.write(ret_json)

        res = CPUnderstandAgentOutputState(
            CGs=state["CGs"],
            input_sources=state["input_sources"],
            vuln_sink_functions=state["vuln_sink_functions"],
        )

        return res
