import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, TypeVar

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.graph import MessagesState, add_messages
from loguru import logger
from pydantic import Field
from typing_extensions import Annotated

from mlla.modules.sanitizer import get_sanitizer_list, get_sanitizer_prompt
from mlla.utils.diff_analyzer import accumulate_diffs, extract_diffs_in_range

from ..agents.bcda_experimental import validate_sanitizers_type
from ..agents.cgpa import CGParserAgent, CGParserInputState
from ..prompts.cpua import CPUA_ERROR
from ..prompts.mcga import (
    STEP1_HUMAN,
    STEP1_SYSTEM,
    STEP1_SYSTEM_WITH_DIFF,
    STEP1_SYSTEM_WITH_INTERESTING_PARENT,
)
from ..utils import (
    find_string_in_file,
    get_callsite,
    instrument_line,
    normalize_func_name,
    normalize_func_name_for_ci,
)
from ..utils.agent import MCGA, TOOL_MODEL, BaseAgentTemplate, ExpectedException
from ..utils.call_extractor import get_all_calls
from ..utils.cg import CG, CalleeRes, FuncInfo, InterestInfo, MCGASinkDetectReport
from ..utils.cg.visitor import (
    CachedResultHandlerVisitor,
    FuncGraph,
    SanitizerValidationReport,
)
from ..utils.context import GlobalContext
from ..utils.execute_llm_code import collect_code_block
from ..utils.llm import LLM
from ..utils.state import merge_with_update
from ..utils.tracer.model import CallState, MethodInfo

T = TypeVar("T")

MAX_EXECUTIONS = 10000  # Maximum number of total executions
MAX_DEPTH = 100


def mcga_create_tag(target_fn: tuple[str, str, str, list[int], tuple[int, int]]) -> str:
    return f"{target_fn[0]}:{target_fn[1]}:{target_fn[3]}:{target_fn[4]}"


def make_mcga_cache_tag(
    node: Optional[FuncInfo],
    cp_name: str,
    harness_name: str,
    target_fn: Optional[tuple[str, str, str, list[int], tuple[int, int]]] = None,
) -> str:
    if node is None and target_fn is None:
        raise ValueError("Either node or target_fn must be provided")

    if node and is_valid_callee_funcinfo(node):
        _target_fn = func_info_to_target_fn(node)
    elif target_fn:
        _target_fn = target_fn
    else:
        raise ValueError("Either node or target_fn must be provided")

    return f"mcga::{cp_name}::{harness_name}::" + mcga_create_tag(_target_fn)


def is_valid_callee_funcinfo(callee_funcinfo: Optional[FuncInfo]) -> bool:
    """Check if the callee info is valid."""
    if (
        callee_funcinfo is not None
        and callee_funcinfo.func_location.file_path
        and callee_funcinfo.func_body
        and callee_funcinfo.func_location.start_line
        and callee_funcinfo.func_location.end_line
    ):
        return True
    return False


async def get_current_cgs_from_redis(
    cpua_target_fns: list[
        tuple[tuple[str, str, str, list[int], tuple[int, int]], list[str]]
    ],
    gc: GlobalContext,
) -> dict[str, list[CG]]:
    from ..agents.cpua import build_cgs_from_entry_points

    cgs = []
    entry_to_fn_list_dict = {}
    for target_fn, entry_to_fn_list in cpua_target_fns:
        cache: list[str] = []
        cg_root_node = get_cg_root_node_from_redis_recursively(target_fn, gc, cache)
        if cg_root_node is None:
            continue
        cg = CG(
            name=cg_root_node.func_location.func_name,
            path=cg_root_node.func_location.file_path,
            root_node=cg_root_node,
        )
        cgs.append(cg)
        entry_to_fn_list_dict[cg.name] = entry_to_fn_list

    cgs_dict = await build_cgs_from_entry_points(
        {gc.cur_harness.name: cgs}, entry_to_fn_list_dict, gc
    )
    return cgs_dict


def get_cg_root_node_from_redis_recursively(
    target_fn: tuple[str, str, str, list[int], tuple[int, int]],
    gc: GlobalContext,
    cache: list[str] = [],
) -> Optional[FuncInfo]:
    tag = make_mcga_cache_tag(
        None,
        gc.cp.name,
        gc.cur_harness.name,
        target_fn,
    )
    cg_str = gc.redis.get(tag)
    if cg_str:
        mcga_funcinfo = MCGAFuncInfo.model_validate_json(cg_str)
        if mcga_funcinfo.create_tag() in cache:
            del mcga_funcinfo.done
            del mcga_funcinfo.in_run
            return mcga_funcinfo

        if mcga_funcinfo.done:
            del mcga_funcinfo.done
            del mcga_funcinfo.in_run
            return mcga_funcinfo
        else:
            children = []
            for child in mcga_funcinfo.children:
                if not is_valid_callee_funcinfo(child):
                    children.append(child)
                    continue

                new_cache = cache.copy()
                new_cache.append(mcga_funcinfo.create_tag())
                child_funcinfo = get_cg_root_node_from_redis_recursively(
                    func_info_to_target_fn(child), gc, new_cache
                )

                if child_funcinfo:
                    children.append(child_funcinfo)
                else:
                    children.append(child)
            mcga_funcinfo.children = children
            return mcga_funcinfo
    else:
        return None


def func_info_to_target_fn(
    callee: FuncInfo,
) -> tuple[str, str, str, list[int], tuple[int, int]]:
    assert callee.func_location.file_path is not None
    assert callee.func_body is not None
    assert callee.func_location.start_line is not None
    assert callee.func_location.end_line is not None
    target_fn = (
        # CGPA will handle normalization of function name.
        callee.func_location.func_name,
        callee.func_location.file_path,
        callee.func_body,
        callee.tainted_args,
        (
            callee.func_location.start_line,
            callee.func_location.end_line,
        ),
    )
    return target_fn


def extract_args(code: str, func_name: str):
    pattern = re.escape(func_name) + r"\s*\("
    match = re.search(pattern, code)
    if not match:
        raise ValueError(f"Function '{func_name}' not found in\n {code}\n")

    start = match.end()
    depth = 1
    i = start
    args = []
    current_arg = ""
    in_string = False
    in_comment = False
    escape = False

    while i < len(code):
        char = code[i]
        next_char = code[i + 1] if i + 1 < len(code) else ""

        # Block comment start
        if not in_string and not in_comment and char == "/" and next_char == "*":
            in_comment = True
            current_arg += char + next_char
            i += 2
            continue

        # Block comment end
        if in_comment:
            current_arg += char
            if char == "*" and next_char == "/":
                current_arg += next_char
                in_comment = False
                i += 2
            else:
                i += 1
            continue

        # String start/end
        if char == '"' and not escape:
            in_string = not in_string
            current_arg += char
            i += 1
            continue

        # Escape handling
        if char == "\\":
            escape = not escape
        else:
            escape = False

        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    if current_arg.strip():
                        args.append(current_arg.strip())
                    break
            elif char == "," and depth == 1:
                args.append(current_arg.strip())
                current_arg = ""
                i += 1
                continue

        current_arg += char
        i += 1

    return args


class MCGAFuncInfo(FuncInfo):
    done: bool = Field(default=False)
    in_run: bool = Field(default=False)


class MakeCallGraphInputState(MessagesState):
    pass


class MakeCallGraphOutputState(MessagesState):
    cg_root_node: Annotated[Optional[FuncInfo], merge_with_update]


class MakeCallGraphOverallState(MakeCallGraphInputState, MakeCallGraphOutputState):
    step: Annotated[int, merge_with_update]
    resolved: Annotated[dict[str, bool], merge_with_update]
    done: Annotated[bool, merge_with_update]
    from_cache: Annotated[bool, merge_with_update]


class MakeCallGraphAgent(BaseAgentTemplate):
    MAX_TIMEOUT = 60 * 60 * 2  # 2hr
    _execution_count: int = 0
    _start_time: float = 0.0
    _execution_lock: asyncio.Lock = asyncio.Lock()
    target_fn: tuple[str, str, str, list[int], tuple[int, int]]  # (fn_name,
    # fn_path, fn_body, tainted_args, fn_loc)
    parent_fn: Optional[FuncInfo]
    cache_for_recursive_paths: dict[str, FuncInfo]  # prevent loop
    current_fn_info: Optional[FuncInfo]
    priority_queue: asyncio.PriorityQueue
    _prev_error_msgs: list[str]

    def __init__(
        self,
        target_fn: tuple[str, str, str, list[int], tuple[int, int]],
        config: GlobalContext,
        cache: dict[str, FuncInfo],
        priority_queue: asyncio.PriorityQueue,
        parent_fn: Optional[FuncInfo] = None,
        current_fn_info: Optional[FuncInfo] = None,
        depth: int = 0,
    ):
        fn_name, _fn_path, _fn_body, _tainted_args, _fn_loc = target_fn
        fn_path = Path(_fn_path).name
        ret_dir = (
            config.RESULT_DIR / MCGA / f"{fn_name}_{fn_path}_{_fn_loc[0]}_{_fn_loc[1]}"
        )
        step_mapper = {
            1: "codeua_understand_step1",
            2: "analyze_children",
        }

        super().__init__(
            config,
            ret_dir,
            MakeCallGraphInputState,
            MakeCallGraphOutputState,
            MakeCallGraphOverallState,
            step_mapper=step_mapper,
            enable_usage_snapshot=False,
            llm_with_tools=os.getenv("MCGA_MODEL", "o4-mini"),
        )

        self._setup_graph()
        self.target_fn = target_fn
        self.parent_fn = parent_fn
        self.depth = depth
        self.cache_for_recursive_paths = cache
        self.priority_queue = priority_queue

        self.llm_sanitizer_validator = LLM(
            model=os.getenv("MCGA_SANITIZER_VALIDATOR_MODEL", "o4-mini"),
            config=config,
            output_format=SanitizerValidationReport,
        )
        self.current_fn_info = current_fn_info
        self._callees_from_parser: list[tuple[str, int, int]] = []
        self._prev_error_msgs = (
            []
        )  # prevent infinite loop by showing error messages only once

    @classmethod
    async def set_start_time(cls) -> None:
        cls.MAX_TIMEOUT = int(os.getenv("MCGA_MAX_TIMEOUT", "7200"))
        logger.info(f"🟢 Set MCGA timeout to {cls.MAX_TIMEOUT} seconds")
        async with cls._execution_lock:
            cls._start_time = time.time()

    @classmethod
    async def check_timeout(cls) -> bool:
        async with cls._execution_lock:
            if time.time() - cls._start_time > cls.MAX_TIMEOUT:
                return True
            return False

    @classmethod
    async def increment_execution_count(cls) -> Optional[int]:
        """Increment the execution count and return the current execution count
        if under limit."""
        async with cls._execution_lock:
            if cls._execution_count >= MAX_EXECUTIONS:
                return None
            cls._execution_count += 1
            return cls._execution_count

    @classmethod
    def get_execution_count(cls) -> int:
        """Get the current execution count."""
        return cls._execution_count

    def serialize(self, state) -> str:
        return self._serialize_cg_root_node(state["cg_root_node"])

    def _serialize_cg_root_node(self, mcga_func_info: MCGAFuncInfo) -> str:
        cg_root_node_dict = {"cg_root_node": mcga_func_info.model_dump()}

        return json.dumps(cg_root_node_dict, indent=2)

    def deserialize(self, state, content: str) -> dict:
        ret = json.loads(content)
        mcga_func_info = MCGAFuncInfo.model_validate(ret["cg_root_node"])
        done = mcga_func_info.done
        del mcga_func_info.done
        del mcga_func_info.in_run
        cg_root_node = mcga_func_info
        if done:
            return {
                "cg_root_node": cg_root_node,
            }
        else:
            # TODO!!!
            return {}

    def _setup_graph(self) -> None:
        """Setup the graph structure with nodes and edges."""

        self.builder.add_node("handle_cache", self.handle_cache)
        self.builder.add_node("codeua_understand_step1", self.code_understand_step1)
        self.builder.add_node("analyze_children", self.analyze_children)

        # self.builder.add_edge("preprocess", "codeua_understand_step1")
        self.builder.add_conditional_edges(
            "preprocess",
            MakeCallGraphAgent.after_preprocess,
            ["finalize", "codeua_understand_step1", "handle_cache"],
        )
        self.builder.add_edge("handle_cache", "finalize")
        self.builder.add_conditional_edges(
            "codeua_understand_step1",
            MakeCallGraphAgent.switch_step2,
            ["analyze_children", TOOL_MODEL, "finalize"],
        )
        self.builder.add_edge("analyze_children", "finalize")

    @staticmethod
    def after_preprocess(state: MakeCallGraphOverallState) -> str:
        if state["from_cache"]:
            return "handle_cache"
        elif "cg_root_node" in state:
            return "finalize"
        else:
            return "codeua_understand_step1"

    def preprocess(self, state: MakeCallGraphInputState) -> MakeCallGraphOverallState:
        cg_root_node = None
        tag = make_mcga_cache_tag(
            self.current_fn_info,
            self.gc.cp.name,
            self.gc.cur_harness.name,
            self.target_fn,
        )
        mcga_funcinfo_str = self.gc.redis.get(tag)
        done = False

        if (cg_root_node := self.cache_for_recursive_paths.get(tag)) is not None:
            logger.debug(f"[MCGA] Cache hit for {self.target_fn[0]}")
            # logger.debug(cg_root_node)
            logger.debug("children: {}", cg_root_node.children)
            return MakeCallGraphOverallState(
                messages=[],
                step=0,
                resolved={"step1": False, "step2": False},
                done=True,
                from_cache=False,
                cg_root_node=cg_root_node,
            )
        elif mcga_funcinfo_str:
            mcga_funcinfo = MCGAFuncInfo.model_validate_json(mcga_funcinfo_str)
            done = mcga_funcinfo.done
            del mcga_funcinfo.done
            del mcga_funcinfo.in_run
            cg_root_node = mcga_funcinfo
            logger.debug(f"[MCGA; done={done}] Redis hit for {self.target_fn[0]}")
            if cg_root_node:
                self.current_fn_info = cg_root_node

                return MakeCallGraphOverallState(
                    messages=[],
                    step=0,
                    resolved={"step1": False, "step2": False},
                    done=done,
                    from_cache=True,
                    cg_root_node=cg_root_node,
                )

        return MakeCallGraphOverallState(
            messages=[],
            step=0,
            resolved={"step1": False, "step2": False},
            done=False,
            from_cache=False,
        )

    async def handle_cache(
        self, state: MakeCallGraphOverallState
    ) -> MakeCallGraphOverallState:
        """Handle the cache."""

        assert self.current_fn_info is not None

        tag = make_mcga_cache_tag(
            self.current_fn_info,
            self.gc.cp.name,
            self.gc.cur_harness.name,
            self.target_fn,
        )

        mcga_funcinfo_str = self.gc.redis.get(tag)
        mcga_funcinfo = MCGAFuncInfo.model_validate_json(mcga_funcinfo_str)
        done = mcga_funcinfo.done
        in_run = mcga_funcinfo.in_run

        if in_run:

            async def _check_until_done(done: bool):
                while not done:
                    mcga_funcinfo_str = self.gc.redis.get(tag)
                    mcga_funcinfo = MCGAFuncInfo.model_validate_json(mcga_funcinfo_str)
                    done = mcga_funcinfo.done
                    if done:
                        del mcga_funcinfo.done
                        del mcga_funcinfo.in_run
                        self.current_fn_info = mcga_funcinfo
                        break
                    await asyncio.sleep(3)

            try:
                await asyncio.wait_for(
                    _check_until_done(done), timeout=self.MAX_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.debug(
                    f"🔴 [{self.current_fn_info.func_location.func_name}] Timeout"
                    f" ({self.MAX_TIMEOUT}) reached"
                )

                if not self.parent_fn:
                    self.priority_queue.put_nowait(
                        (100000000000000, 100000000000000, 100000000000000, None)
                    )
                return MakeCallGraphOverallState(
                    messages=[],
                    step=0,
                    resolved={"step1": False, "step2": False},
                    done=False,
                    from_cache=True,
                    cg_root_node=self.current_fn_info,
                )
        else:
            # this cache was from the previous run
            if done:
                pass
            else:
                self._record_cg_root_node(self.current_fn_info, done=False)
                self._set_loop_cache()
                await self.analyze_children(state)

        assert self.current_fn_info is not None

        func_graph = FuncGraph(self.current_fn_info, {})
        await func_graph.async_traverse(CachedResultHandlerVisitor(self.gc))

        self._log_call_graph(
            self.current_fn_info,
            f"[HANDLE_CACHE; done={done}; in_run={in_run}]",
        )

        return MakeCallGraphOverallState(
            messages=[],
            step=0,
            resolved={"step1": False, "step2": False},
            done=done,
            from_cache=True,
            cg_root_node=self.current_fn_info,
        )

    #########################################################
    # Step 1: code_understand_step1
    #########################################################
    async def code_understand_step1(
        self, state: MakeCallGraphOverallState
    ) -> MakeCallGraphOverallState:
        """Process step 1 of code understanding."""
        if self.current_fn_info is None:
            current_fn_info = await self._create_current_fn_info()
            if current_fn_info is None:
                logger.warning(f"cg_root_node is None. target_fn: {self.target_fn}")
                return MakeCallGraphOverallState(
                    messages=[],
                    step=1,
                    resolved={"step1": True, "step2": False},
                    cg_root_node=None,
                )
            self.current_fn_info = current_fn_info
        self._update_cg_root_node(self.current_fn_info)
        self._update_callees_from_parser()
        self._update_interest_info(self.current_fn_info)

        # logger.info(
        #     f"[MCGA; {self.current_fn_info.func_location.func_name}] Step 1:
        #     {state['step']}"
        # )

        if state["step"] == 1:
            return await self._handle_step1_response(state)

        return self._prepare_step1_messages()

    #########################################################
    # Step 1.1: create current function info
    #########################################################
    async def _create_current_fn_info(self) -> Optional[FuncInfo]:
        """Create the current function info."""
        fn_name, fn_path, fn_body, _tainted_args, fn_loc = self.target_fn
        cgpa = CGParserAgent(self.gc)
        cgpa_graph = cgpa.compile()

        if self.parent_fn:
            cgpa_input_state = CGParserInputState(
                fn_name=fn_name,
                fn_file_path=fn_path,
                caller_file_path=self.parent_fn.func_location.file_path,
                caller_fn_body=self.parent_fn.func_body,
                callsite_range=(
                    self.parent_fn.func_location.start_line,
                    self.parent_fn.func_location.end_line,
                ),
            )
        else:
            cgpa_input_state = CGParserInputState(
                fn_name=fn_name,
                fn_file_path=fn_path,
                caller_file_path=self.gc.cur_harness.src_path.as_posix(),
            )

        cgpa_state = await cgpa_graph.ainvoke(cgpa_input_state)

        current_fn_info: Optional[FuncInfo] = cgpa_state["code_dict"]
        if current_fn_info is None:
            logger.error(f"current_fn_info is None. target_fn: {self.target_fn}")
            return None

        return current_fn_info

    #########################################################
    # Step 1.2: handle step1 response
    #########################################################
    async def _handle_step1_response(
        self, state: MakeCallGraphOverallState
    ) -> MakeCallGraphOverallState:
        _state, existing_callees, report, callee_info_dict = (
            await self._verify_step1_response(state)
        )
        if _state is not None:
            return _state

        assert report is not None

        callee_list = self._dedup_and_sort_callee_list(
            report.callsites, existing_callees
        )

        assert self.current_fn_info is not None

        self._update_children_before(
            self.current_fn_info, callee_list, callee_info_dict, {}
        )
        self.current_fn_info.sink_detector_report = report.to_sink_detect_report()

        def _node_checker(fn_info: FuncInfo) -> FuncInfo:
            fn_info.call_recursive(FuncInfo.check_and_make_abs_file_path)
            return fn_info

        try:
            self.current_fn_info = _node_checker(self.current_fn_info)
        except Exception as e:
            logger.error(f"Error: {e}")

        parent_fn_name = (
            self.parent_fn.func_location.func_name if self.parent_fn else None
        )

        logger.info(
            f"mcga_step1(depth={self.depth}, cnt={self.get_execution_count()},"
            f" parent_fn={parent_fn_name}):"
            f" {self.target_fn[0]}"
        )

        self._log_call_graph(self.current_fn_info, "[MCGA]")

        self._set_loop_cache()

        self._record_cg_root_node(self.current_fn_info, done=False)

        await self._check_cg_root_node_status(self.current_fn_info)

        return MakeCallGraphOverallState(
            messages=[RemoveMessage(id=m.id) for m in state["messages"]],
            step=1,
            resolved={"step1": True, "step2": False},
            done=False,
        )

    def _set_loop_cache(self):
        if self.current_fn_info.create_tag() not in self.cache_for_recursive_paths:
            self.cache_for_recursive_paths[self.current_fn_info.create_tag()] = (
                self.current_fn_info.model_copy(deep=True)
            )
        tag = make_mcga_cache_tag(
            self.current_fn_info,
            self.gc.cp.name,
            self.gc.cur_harness.name,
            self.target_fn,
        )
        if tag not in self.cache_for_recursive_paths:
            self.cache_for_recursive_paths[tag] = self.current_fn_info.model_copy(
                deep=True
            )

    #########################################################
    # Step 1.2.1: verify step1 response
    #########################################################
    async def _verify_step1_response(self, state: MakeCallGraphOverallState) -> tuple[
        Optional[MakeCallGraphOverallState],
        dict[str, tuple[str, str]],
        Optional[MCGASinkDetectReport],
        dict[str, FuncInfo],
    ]:
        """Handle the response from step 1."""
        last_message = state["messages"][-1]
        report = None

        fn_name, fn_path, fn_body, _tainted_args, fn_loc = self.target_fn

        try:
            report = await self._verify_step1_format(last_message.content, fn_path)
        except ExpectedException as e:
            msg = CPUA_ERROR.format(error=e)
            state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
            return state, {}, None, {}
        except Exception as e:
            logger.warning(f"Error: {e}")
            import traceback

            logger.warning(traceback.format_exc())
            msg = CPUA_ERROR.format(error=e)
            state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
            return state, {}, None, {}

        # for c in report.callsites:
        #     if c.line_range[0][0] < fn_loc[0] or c.line_range[0][0] > fn_loc[1]:
        #         logger.error(f"[{id(self)}] callsite out of {fn_path}: {report}")

        error_msgs = []

        try:
            # logger.info(f"[{id(self)}] sink verification: {report}")
            self._verify_valid_sink_line(report)
        except ExpectedException as e:
            # logger.warning(f"[{id(self)}] sink invalid error: {e}")
            if str(e) not in self._prev_error_msgs:
                error_msgs.append(str(e))
                self._prev_error_msgs.append(str(e))
        except Exception as e:
            logger.warning(f"Error: {e}")

        if report.callsites == [] and len(self._callees_from_parser) > 0:
            error_msg = (
                f"Are you sure that the function `{self.target_fn[0]}` does not have"
                f" any callsites? I found {len(self._callees_from_parser)} function"
                " calls in the code. Please review the <callees> section carefully."
            )
            error_msgs.append(error_msg)

        callee_name_file_line_set: list[tuple[str, str, str, int]] = []

        if self.gc.recent_tracer_result:
            callees_to_analyze: set[CallState] = (
                self.gc.recent_tracer_result.find_callees_by_caller_name_and_path(
                    normalize_func_name(fn_name), fn_path, fn_loc
                )
            )

            for cs in callees_to_analyze:
                if isinstance(cs.callee, MethodInfo):
                    callee_name = cs.callee.method_name
                    if callee_name == "<init>":
                        callee_name = (
                            cs.callee.class_name.split(".")[-1] + "." + callee_name
                        )
                    elif callee_name == "<clinit>":
                        continue
                else:
                    callee_name = cs.callee.function_name
                callee_name_file_line_set.append(
                    (callee_name, cs.callee.file, cs.file, cs.line)
                )

        callee_list = self._update_recent_callee_list(
            report.callsites, callee_name_file_line_set
        )
        report.callsites = callee_list

        try:
            callee_info_dict = await self._verify_callsites_return_callee_info_dict(
                callee_list, fn_path
            )
        except ExpectedException as e:
            error_msgs.append(str(e))
        except Exception as e:
            logger.warning(f"Error: {e}")
            import traceback

            logger.warning(traceback.format_exc())
            error_msgs.append(str(e))

        real_error_msg = "\n".join(error_msgs)

        if real_error_msg:
            msg = CPUA_ERROR.format(error=real_error_msg)
            state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
            logger.warning(f"[{self.target_fn[0]}] Error: {real_error_msg}")
            return state, {}, None, {}

        existing_callees, non_existing_callees = (
            await self._check_callees_in_tracer_result(
                callee_name_file_line_set, callee_list, callee_info_dict
            )
        )

        if non_existing_callees:
            logger.warning(
                f"[{self.target_fn[0]}] Non-existing callees: {non_existing_callees}"
            )
            logger.warning(
                f"[{self.target_fn[0]}] Existing callees: {existing_callees}"
            )
            logger.warning(f"[{self.target_fn[0]}] Callee_list: {callee_list}")
            logger.warning(
                f"[{self.target_fn[0]}] Callee_info_dict: {callee_info_dict}"
            )
            error_msg = (
                "Below callees are in the execution trace. Please include"
                f" them as callees of `{self.target_fn[0]}` regardless of tainted"
                " arguments\n"
            )
            for name, line in non_existing_callees:
                error_msg += f"- {name} at Line {line}\n"

            if error_msg not in self._prev_error_msgs:
                self._prev_error_msgs.append(error_msg)
                msg = CPUA_ERROR.format(error=error_msg)
                state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
                return state, {}, None, {}

        return None, existing_callees, report, callee_info_dict

    #########################################################
    # Step 1.2.1.1: verify step1 response format
    #########################################################

    async def _verify_step1_format(
        self, content: str, fn_path: str
    ) -> MCGASinkDetectReport:
        parser = PydanticOutputParser(pydantic_object=MCGASinkDetectReport)
        try:
            json_contents = collect_code_block(content, "json")
            if not json_contents:
                json_contents = [content]
            report = parser.parse(json_contents[0])
        except Exception as e:
            raise ExpectedException(f"response content is not valid json: {e}")

        possible_sanitizers: set[str] = set(
            [
                x
                for sanitizer_type in self.gc.get_sanitizer_type()
                for x in get_sanitizer_list(sanitizer_type)
            ]
        )

        report.sanitizer_candidates = validate_sanitizers_type(
            self.llm_sanitizer_validator,
            report.sanitizer_candidates,
            list(possible_sanitizers),
            report.sink_analysis_message,
        )

        return report

    def _verify_valid_sink_line(self, report: MCGASinkDetectReport):
        if report.is_vulnerable:
            fn_name, _, fn_body, _, fn_loc = self.target_fn

            if (
                report.sink_line_number < fn_loc[0]
                or report.sink_line_number > fn_loc[1]
            ):
                raise ExpectedException(
                    f"Invalid sink line number: {report.sink_line_number}. The function"
                    f" {fn_name} has {fn_loc[0]} - {fn_loc[1]} lines. Review the sink"
                    " analysis result and callee list carefully. If the sink line"
                    " isn’t in the function body, please review the callee list and"
                    " provide the intermediate functions needed to reach the sink"
                    " line. Otherwise, if the sink line isn’t related to the function"
                    " body, return False to is_vulnerable."
                )

            relative_line = report.sink_line_number - fn_loc[0]
            _sink_line = fn_body.splitlines()[relative_line]

            if (
                report.sink_line.strip() not in _sink_line
                and _sink_line.strip() not in report.sink_line
            ):
                raise ExpectedException(
                    f"Invalid sink line: {report.sink_line}. The function {fn_name} has"
                    f" {_sink_line} at line {report.sink_line_number}. Review the sink"
                    " analysis result carefully."
                )

    #########################################################
    # Step 1.2.1.1.1: verify callsites in the response format verifier
    #########################################################
    async def _verify_callsites_return_callee_info_dict(
        self, callee_list: list[CalleeRes], fn_path: str
    ) -> dict[str, FuncInfo]:
        with open(fn_path, "r") as f:
            fn_body = f.read()

        fn_body_lines = fn_body.splitlines()

        cgpa = CGParserAgent(self.gc)
        cgpa_graph = cgpa.compile()

        callee_info_dict = {}
        tasks = []

        for callee in callee_list:
            start_line = max(callee.line_range[0][0] - 1, 0)
            end_line = min(callee.line_range[1][0] + 1, len(fn_body_lines))

            cgpa_state_task = asyncio.create_task(
                cgpa_graph.ainvoke(
                    CGParserInputState(
                        fn_name=callee.name,
                        caller_file_path=self.target_fn[1],
                        caller_fn_body=self.target_fn[2],
                        callsite_location=callee.line_range[0],
                        callsite_range=(start_line, end_line),
                    ),
                )
            )
            tasks.append(cgpa_state_task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for cgpa_state, callee in zip(results, callee_list):
            # start_line = max(callee.line_range[0][0] - 1, 0)
            # end_line = min(callee.line_range[1][0] + 1, len(fn_body_lines))
            # callsite_code = "\n".join(fn_body_lines[start_line - 1 : end_line])

            if isinstance(cgpa_state, Exception):
                import traceback

                tb_lines = traceback.format_exception(
                    type(cgpa_state), cgpa_state, cgpa_state.__traceback__
                )
                logger.error("".join(tb_lines))
                continue

            func_info: Optional[FuncInfo] = cgpa_state["code_dict"]

            if func_info:
                callee_info_dict[callee.create_tag()] = func_info

            # args_list = []
            # if func_info:
            #     if func_info.func_signature:
            #         func_signature = func_info.func_signature
            #     elif func_info.func_body:
            #         lines = []
            #         for line in func_info.func_body.splitlines():
            #             if ")" in line:
            #                 lines.append(line)
            #                 break
            #             lines.append(line)
            #         func_signature = "".join(lines)
            #     else:
            #         func_signature = func_info.func_location.func_name

            #     if "(" in func_signature and ")" in func_signature:
            #         args = func_signature.split("(")[1].rsplit(")", 1)[0]
            #         args_list = args.split(",")
            # if (
            #     not args_list
            #     and normalize_func_name_for_ci(callee.name) in callsite_code
            # ):
            #     args_list = extract_args(
            #         callsite_code, normalize_func_name_for_ci(callee.name)
            #     )
            # if not args_list and ("<init>" in callee.name or "<clinit>" in
            # callee.name):
            #     if "this" in callsite_code or "super" in callsite_code:
            #         continue
            #     # normalized_callee_name = normalize_func_name_for_ci(callee.name)

            # if args_list:
            #     args_len = len(args_list)
            #     if any(
            #         [
            #             arg_idx < 0 or arg_idx >= args_len
            #             for arg_idx in callee.tainted_args
            #         ]
            #     ):
            #         raise ExpectedException(
            #             f"Invalid tainted arg index: {callee.tainted_args}.
            #             The callee"
            #             f" `{callee.name}` has {args_len} arguments.\ncallsite_code:"
            #             f" {callsite_code}\nfn_path: {fn_path}"
            #         )
            # elif len(callee.tainted_args) > 0:
            #     raise ExpectedException(
            #         f"Failed to find the tainted args of the callee `{callee.name}`"
            #         " in the callsite code.\n"
            #         f"callsite_code: {callsite_code}\n"
            #         f"fn_path: {fn_path}"
            #     )

        return callee_info_dict

    #########################################################
    # Step 1.2.2: deduplicate and sort callee list
    #########################################################

    def _dedup_and_sort_callee_list(
        self, callee_list: list[CalleeRes], existing_callees: dict[str, tuple[str, str]]
    ) -> list[CalleeRes]:
        """Deduplicate the callee list."""
        callee_dict: dict[str, CalleeRes] = {}

        # deduplicate callee_list
        for callee in callee_list:
            dict_tag = f"{callee.name}:{callee.tainted_args}"
            if callee.create_tag() in existing_callees:
                callee_dict[dict_tag] = callee
            elif dict_tag not in callee_dict:
                callee_dict[dict_tag] = callee

        new_callee_list = list(callee_dict.values())

        # Prioritize callees that are not in the existing_callees
        # priority = sorted(
        #     [c for c in new_callee_list if c.create_tag() in existing_callees],
        #     key=lambda x: x.line_range[1][0],
        # )
        # rest = sorted(
        #     [c for c in new_callee_list if c.create_tag() not in existing_callees],
        #     key=lambda x: x.line_range[1][0],
        # )
        res = sorted(
            new_callee_list,
            key=lambda x: x.priority,
            reverse=True,
        )
        return res

    def _log_result(self, report: MCGASinkDetectReport):
        """Log the result of sink detection"""
        if report.is_vulnerable:
            logger.info(
                f"Sink analysis result: {self.target_fn[0]}:"
                f" {report.sink_line}:{report.sink_line_number}"
            )
            logger.info(f"Sink analysis message: {report.sink_analysis_message}")
        else:
            logger.debug(f"No sink found for {self.target_fn[0]}")

    @staticmethod
    def switch_step2(
        state: MakeCallGraphOverallState,
    ) -> str:
        if state["resolved"].get("step1", False):
            if "cg_root_node" in state and state["cg_root_node"] is None:
                return "finalize"
            return "analyze_children"
        return TOOL_MODEL

    async def _update_callee_info_dict(
        self,
        callee: FuncInfo,
    ) -> Optional[tuple[str, FuncInfo]]:
        """Process a single callee and update the call graph."""

        callee_cg_node: Optional[FuncInfo] = None
        # callee_info is just a funcinfo for a single function. It doesn't have
        # children.
        if is_valid_callee_funcinfo(callee):
            assert callee is not None
            if callee.create_tag() in self.cache_for_recursive_paths:
                callee_cg_node = self.cache_for_recursive_paths[callee.create_tag()]
                logger.debug(
                    f"Loop cache hit for {callee.func_location.func_name}:"
                    f" {callee_cg_node.children}"
                )
            else:
                # This function calls MCGA recursively.
                callee_cg_node = await self._create_callee_cg_node(callee)

            if callee_cg_node:
                return callee.create_tag(), callee_cg_node

            return callee.create_tag(), callee

        return None

    async def _check_cg_root_node_status(self, cg_root_node: FuncInfo) -> bool:
        """Check if the children should be analyzed."""
        # XXX: 1. Stop if current function is vulnerable.
        if (
            cg_root_node.sink_detector_report
            and cg_root_node.sink_detector_report.is_vulnerable
        ):
            logger.info(
                "🔴 [TODO] Shall we stop analyzing children because the current"
                f" function ({self.target_fn[0]}) is vulnerable?:"
                f" {cg_root_node.sink_detector_report.sanitizer_candidates}"
            )
            current_cgs = await get_current_cgs_from_redis(
                self.gc.cpua_target_fns, self.gc
            )
            if self.gc.candidate_queue:
                logger.info(f"Putting {len(current_cgs)} CGs to the queue")
                # for cg_list in current_cgs.values():
                #     for cg in cg_list:
                #         logger.info(f"CG: {cg.root_node.format_recursive()}")
                self.gc.candidate_queue.put({"CGs": current_cgs})

        # 2. Stop if current function is in the diff file.
        fn_name, fn_path, fn_body, tainted_args, fn_loc = self.target_fn

        if cg_root_node.interest_info and cg_root_node.interest_info.is_interesting:
            logger.info(
                "🟡 [TODO] Shall we stop analyzing children because the current"
                f" function ({self.target_fn[0]}) is interesting?"
            )

        # TODO: 3. Stop if current function is in the sarif report.
        return False

    async def _create_callee_cg_node(
        self,
        callee: FuncInfo,
    ) -> Optional[FuncInfo]:
        """Process a valid callee and update the call graph."""

        target_fn = func_info_to_target_fn(callee)

        mcga = MakeCallGraphAgent(
            target_fn,
            self.gc,
            self.cache_for_recursive_paths.copy(),
            self.priority_queue,
            depth=self.depth + 1,
            parent_fn=self.current_fn_info,
            current_fn_info=callee,
        )
        graph = mcga.compile()
        mcga_state = await graph.ainvoke({"messages": []}, self.gc.graph_config)

        cg_root_node: Optional[FuncInfo] = mcga_state["cg_root_node"]
        return cg_root_node

    def _log_call_graph(self, cg_root_node: FuncInfo, prefix: str = "") -> None:
        """Log the call graph structure."""
        logger.info(
            f"{prefix} {cg_root_node.func_location.func_name}:"
            f" {cg_root_node.func_location.file_path}:"
            f" {cg_root_node.func_location.start_line}-"
            f"{cg_root_node.func_location.end_line}"
        )
        for child in cg_root_node.children:
            logger.info(
                f"   - {child.func_location.func_name}:"
                f" {child.func_location.file_path}:"
                f" {child.func_location.start_line}-{child.func_location.end_line}:"
                f" {len(child.children)}"
            )

    async def _check_callees_in_tracer_result(
        self,
        callee_name_file_line_set: list[tuple[str, str, str, int]],
        callee_list: list[CalleeRes],
        callee_info_dict: dict[str, FuncInfo],
    ) -> tuple[
        dict[str, tuple[str, str]], list[tuple[str, int]]
    ]:  # (existing_callees, non_existing_callees)
        # existing_callees: dict[callee_tag, (callee_name, callee_file_path)]
        # non_existing_callees: list[(callee_name, callsite_line)]
        non_existing_callees = []
        existing_callees = {}
        fn_name, fn_path, fn_body, _tainted_args, fn_loc = self.target_fn

        cgpa = CGParserAgent(self.gc)
        cgpa_graph = cgpa.compile()

        for (
            callee_name,
            callee_file_path,
            caller_file_path,
            callsite_line,
        ) in callee_name_file_line_set:
            found = False
            for callee in callee_list:
                if normalize_func_name_for_ci(
                    callee.name
                ) == normalize_func_name_for_ci(callee_name):
                    callee_info = callee_info_dict.get(callee.create_tag())
                    found = True
                    if (
                        callee_info
                        and Path(callee_file_path).is_absolute()
                        and callee_info.func_location.file_path != callee_file_path
                    ):
                        start_line = callee.line_range[0][0] - 1
                        end_line = callee.line_range[1][0] + 1
                        logger.warning(
                            f"Callee {callee_name} is not in the file path"
                            f" {callee_file_path},"
                            f" {callee_info.func_location.file_path}"
                        )
                        # TODO
                        cgpa_state = await cgpa_graph.ainvoke(
                            CGParserInputState(
                                fn_name=callee.name,
                                fn_file_path=callee_file_path,
                                caller_file_path=caller_file_path,
                                caller_fn_body=fn_body,
                                callsite_location=callee.line_range[0],
                                callsite_range=(start_line, end_line),
                            ),
                        )
                        if cgpa_state["code_dict"]:
                            callee_info = cgpa_state["code_dict"]
                            callee_info_dict[callee.create_tag()] = cgpa_state[
                                "code_dict"
                            ]
                    if callee_info and callee_info.func_location.file_path:
                        existing_callees[callee.create_tag()] = (
                            callee_name,
                            callee_info.func_location.file_path,
                        )
                    else:
                        existing_callees[callee.create_tag()] = (
                            callee_name,
                            callee_file_path,
                        )
                    break
            if not found:
                callsite = get_callsite(caller_file_path, callsite_line)
                if normalize_func_name_for_ci(callee_name) in callsite:
                    logger.info(f"🟡 {callee_name} is not in the callee list")
                    non_existing_callees.append((callee_name, callsite_line))

        return existing_callees, non_existing_callees

    #########################################################
    # Step 1.3: prepare step 1 messages
    #########################################################
    def _prepare_step1_messages(self) -> MakeCallGraphOverallState:
        """Prepare the initial request for step 1."""
        fn_name, fn_path, fn_body, _tainted_args, fn_loc = self.target_fn

        if _tainted_args == [] and self.gc.cp.language == "jvm":
            tainted_args = "all args are tainted"
        else:
            tainted_args = str(_tainted_args)

        instrumented_fn_body, _ = instrument_line(fn_body, fn_loc[0])
        sanitizer_prompt = get_sanitizer_prompt(self.gc.get_sanitizer_type())

        contain_diff = False
        parent_is_interesting_but_not_in_diff = False

        if fn_path in self.gc.function_diffs:
            diffs_in_range = extract_diffs_in_range(
                self.gc.function_diffs[fn_path],
                fn_loc[0],
                fn_loc[1],
                fn_path,
            )

            diff_str = accumulate_diffs(diffs_in_range, in_mcga=True)

            if diff_str:
                instrumented_fn_body += f"\n<diff>\n{diff_str}\n</diff>"
                contain_diff = True
                tainted_args = "all args are tainted"

        if (
            not contain_diff
            and self.parent_fn
            and self.parent_fn.interest_info
            and self.parent_fn.interest_info.is_interesting
        ):
            parent_is_interesting_but_not_in_diff = True
            tainted_args = "all args are tainted"

        system_msg = (
            STEP1_SYSTEM_WITH_DIFF.format(
                sanitizer_prompt=sanitizer_prompt, project_dir=self.gc.cp.cp_src_path
            )
            if contain_diff
            else (
                STEP1_SYSTEM_WITH_INTERESTING_PARENT.format(
                    sanitizer_prompt=sanitizer_prompt,
                    project_dir=self.gc.cp.cp_src_path,
                )
                if parent_is_interesting_but_not_in_diff
                else STEP1_SYSTEM.format(
                    sanitizer_prompt=sanitizer_prompt,
                    project_dir=self.gc.cp.cp_src_path,
                )
            )
        )

        extra_callees = ""
        found_callee_idx_lst = []

        if self.gc.recent_tracer_result:
            callees_to_analyze: set[CallState] = (
                self.gc.recent_tracer_result.find_callees_by_caller_name_and_path(
                    normalize_func_name(fn_name), fn_path, fn_loc
                )
            )
            callee_name_file_line_set: list[tuple[str, str, str, int]] = []

            for cs in callees_to_analyze:
                if isinstance(cs.callee, MethodInfo):
                    callee_name = cs.callee.method_name
                    if callee_name == "<init>":
                        callee_name = (
                            cs.callee.class_name.split(".")[-1] + "." + callee_name
                        )
                    elif callee_name == "<clinit>":
                        continue
                else:
                    callee_name = cs.callee.function_name
                callee_name_file_line_set.append(
                    (callee_name, cs.callee.file, cs.file, cs.line)
                )

            for (
                callee_name,
                _callee_file_path,
                _caller_file_path,
                callsite_line,
            ) in callee_name_file_line_set:
                found = False
                for i, (name, line, col) in enumerate(self._callees_from_parser):
                    if name == callee_name and line == callsite_line:
                        found = True
                        found_callee_idx_lst.append(i)
                        break
                if not found:
                    extra_callees += (
                        f"- {callee_name} at Line {callsite_line} << MUST INCLUDE"
                        " THIS\n"
                    )

        if extra_callees:
            tainted_args = "all args are tainted"

        callees_str = ""

        for i, (name, line, col) in enumerate(self._callees_from_parser):
            if i in found_callee_idx_lst:
                callees_str += (
                    f"- {name} at Line {line} and Col {col} << MUST INCLUDE THIS\n"
                )
            else:
                callees_str += f"- {name} at Line {line} and Col {col}\n"

        callees = callees_str + "\n" + extra_callees

        parent_fn_name = ""
        parent_fn_path = ""
        parent_fn_body = ""

        if self.parent_fn and self.parent_fn.func_location.func_name:
            parent_fn_name = (
                "<parent_fn_name>"
                + self.parent_fn.func_location.func_name
                + "</parent_fn_name>"
            )
        if self.parent_fn and self.parent_fn.func_location.file_path:
            parent_fn_path = (
                "<parent_fn_path>"
                + self.parent_fn.func_location.file_path
                + "</parent_fn_path>"
            )
        if self.parent_fn and self.parent_fn.func_body:
            parent_fn_body = (
                "<parent_fn_body>" + self.parent_fn.func_body + "</parent_fn_body>"
            )

        messages = [
            SystemMessage(system_msg),
            HumanMessage(
                STEP1_HUMAN.format(
                    parent_fn_name=parent_fn_name,
                    parent_fn_path=parent_fn_path,
                    parent_fn_body=parent_fn_body,
                    fn_name=fn_name,
                    fn_path=fn_path,
                    fn_body=instrumented_fn_body,
                    callees=callees,
                    tainted_args=tainted_args,
                )
            ),
        ]
        # logger.info(f"[{id(self)}] step1 messages: {messages}")

        # FOR DEBUG
        # from mlla.utils.artifact_storage import store_artifact_files
        # cp_name = self.gc.cp.name
        # harness_name = self.gc.target_harness
        # base_path = Path(f"/mlla/mcga_{harness_name}")
        # this_path = base_path / f"mcga_{fn_name}_{id(messages)}"
        # this_path.parent.mkdir(parents=True, exist_ok=True)
        # store_artifact_files(
        #     base_path=this_path,
        #     prompts=messages,
        # )
        # logger.success(f"logging files to: {this_path}")

        return MakeCallGraphOverallState(
            messages=messages,
            step=1,
            resolved={"step1": False, "step2": False},
        )

    #########################################################
    # Step 2: analyze children
    #########################################################
    async def analyze_children(
        self, state: MakeCallGraphOverallState
    ) -> MakeCallGraphOverallState:
        # This function explores children of current function, so I put stop condition
        # here.
        do_not_continue = False

        if self.depth > MAX_DEPTH:
            logger.error(f"🔴 Maximum depth ({MAX_DEPTH}) reached")
            do_not_continue = True
        if await self.check_timeout():
            logger.error(f"🔴 Timeout ({self.MAX_TIMEOUT}) reached")
            do_not_continue = True
        if not (execution_count := await self.increment_execution_count()):
            logger.error(f"🔴 Maximum execution count ({MAX_EXECUTIONS}) reached")
            do_not_continue = True

        assert self.current_fn_info is not None

        # if self.gc.in_ci:
        # in CI, we don't want to keep running main thread.
        # done = True
        # else:
        done = False

        if not do_not_continue:
            assert execution_count is not None
            self._analyze_children_recursively(
                self.current_fn_info,
                execution_count,
            )
            done = False

        return MakeCallGraphOverallState(
            messages=[],
            step=2,
            resolved={"step1": True, "step2": True},
            cg_root_node=self.current_fn_info,
            done=done,
        )

    #########################################################
    # Step 2.1: analyze children recursively
    #########################################################
    def _analyze_children_recursively(
        self,
        cg_root_node: FuncInfo,
        execution_count: int,
    ) -> None:
        """Analyze the children of the call graph root node recursively."""
        callee_list: list[FuncInfo] = cg_root_node.children

        callee_list = [
            child
            for child in callee_list
            if (
                is_valid_callee_funcinfo(child)
                and cg_root_node.create_tag() != child.create_tag()
            )
        ]

        if not callee_list:
            self._record_cg_root_node(cg_root_node, done=True)
            logger.info(f"🟢 {cg_root_node.func_location.func_name} (done: True)")
            return

        async def _check_if_all_children_done_and_record(
            cg_root_node: FuncInfo,
        ) -> None:
            print_once = True
            while True:
                undone_children = [
                    child
                    for child in cg_root_node.children
                    if not self._check_if_node_done(child)
                    and cg_root_node.create_tag() != child.create_tag()
                    and is_valid_callee_funcinfo(child)
                ]
                if not undone_children:
                    self._record_cg_root_node(cg_root_node, done=True)
                    logger.info(
                        f"🟢 {cg_root_node.func_location.func_name} (done: True)"
                    )
                    break
                else:
                    if time.time() - self._start_time > self.MAX_TIMEOUT:
                        if print_once:
                            logger.info(
                                f"🟡 {cg_root_node.func_location.func_name} (done:"
                                " False):"
                            )
                            for child in undone_children:
                                logger.info(f"🟡 - {child.func_location.func_name}")
                            print_once = False
                        await asyncio.sleep(10)
                    await asyncio.sleep(5)

        def _handle_task_result(task):
            try:
                if task.cancelled():
                    return
                callee_tag_and_funcinfo = task.result()
                if callee_tag_and_funcinfo:
                    tag, callee_info_or_cg_node = callee_tag_and_funcinfo
                    self._update_children_after(
                        cg_root_node,
                        tag,
                        callee_info_or_cg_node,
                    )

            except Exception as e:
                logger.error(f"Error in task result: {e}")
                import traceback

                tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
                logger.error("".join(tb_lines))

        for idx, callee in enumerate(callee_list):

            task = asyncio.ensure_future(
                self._update_callee_info_dict(
                    callee,
                )
            )
            task.add_done_callback(lambda t: _handle_task_result(t))

            # priority: (order, depth, execution_count)
            self.priority_queue.put_nowait((idx, self.depth, execution_count, task))

        async def _wait_for_all_children_done(cg_root_node: FuncInfo):
            try:
                await asyncio.wait_for(
                    _check_if_all_children_done_and_record(cg_root_node),
                    timeout=self.MAX_TIMEOUT,
                )
            except asyncio.TimeoutError:
                if not self.parent_fn:
                    self.priority_queue.put_nowait(
                        (100000000000000, 100000000000000, 100000000000000, None)
                    )
                logger.debug(f"🔴 Timeout ({self.MAX_TIMEOUT}) reached")

        asyncio.create_task(_wait_for_all_children_done(cg_root_node))

    def _update_recent_callee_list(
        self,
        callee_list: list[CalleeRes],
        callee_name_file_line_set: list[tuple[str, str, str, int]],
    ) -> list[CalleeRes]:
        res: list[CalleeRes] = []
        for _callee_name, _callee_line, _callee_col in self._callees_from_parser:
            found = False
            for callee in callee_list:
                callee_name = callee.name
                callee_line = callee.line_range[0][0]
                if callee_name == _callee_name and callee_line == _callee_line:
                    res.append(callee)
                    found = True
                    break
            if not found:
                res.append(
                    CalleeRes(
                        name=_callee_name,
                        line_range=(
                            (_callee_line, _callee_col),
                            (_callee_line, _callee_col + len(_callee_name)),
                        ),
                        tainted_args=[],
                    )
                )

        for name, _, caller_file_path, line in callee_name_file_line_set:
            normalized_name = normalize_func_name_for_ci(name)
            if any(
                (callee.name, callee.line_range[0][0]) == (normalized_name, line)
                for callee in res
            ):
                continue
            else:
                added = False
                for callee in callee_list:
                    if (
                        callee.name == normalized_name or callee.name == name
                    ) and callee.line_range[0][0] == line:
                        res.append(callee)
                        added = True
                if added:
                    continue
            if positions := find_string_in_file(
                caller_file_path, normalized_name, line
            ):
                for pos in positions:
                    res.append(
                        CalleeRes(
                            name=name,
                            line_range=(
                                (pos[0], pos[1]),
                                (pos[0], pos[1] + len(normalized_name)),
                            ),
                            tainted_args=[],
                        )
                    )
                    break

        for callee in callee_list:
            if callee.name not in [r.name for r in res]:
                res.append(callee)

        return list(res)

    def _update_callees_from_parser(self) -> None:
        fn_name, fn_path, fn_body, _tainted_args, fn_loc = self.target_fn
        dedup_dict: dict[str, tuple[str, int, int]] = {}
        try:
            _callees, from_file_path = get_all_calls(fn_path, fn_body)

            dedup_dict = dict(
                [
                    (
                        node.text.decode("utf8"),
                        (
                            node.text.decode("utf8"),
                            (
                                node.range.start_point.row + 1
                                if from_file_path
                                else node.range.start_point.row + fn_loc[0]
                            ),
                            node.range.start_point.column,
                        ),
                    )
                    for node in _callees
                    if node.text
                ]
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            pass

        self._callees_from_parser = list(dedup_dict.values())

    def _update_cg_root_node(self, cg_root_node: FuncInfo) -> None:
        """Update the call graph root node properties."""
        cg_root_node.need_to_analyze = True
        cg_root_node.tainted_args = self.target_fn[3]
        cg_root_node.func_location.func_name = self.target_fn[0]
        if cg_root_node.func_location.file_path != self.target_fn[1]:
            logger.warning(
                "CG root node file path"
                f" {cg_root_node.func_location.file_path} does not match the target"
                f" function file path {self.target_fn[1]}"
            )
            if not cg_root_node.func_location.file_path:
                cg_root_node.func_location.file_path = self.target_fn[1]

    def _update_children_before(
        self,
        cg_root_node: FuncInfo,
        callee_list: list[CalleeRes],
        callee_info_dict: dict[str, FuncInfo],
        backup_callee_info_dict: dict[str, FuncInfo],
    ) -> None:
        """Update the children of the call graph root node."""
        children = []
        for callee in callee_list:
            # callee is the callee info we got from caller function.
            if callee.create_tag() in callee_info_dict:
                # callee_info_dict is more detailed as it is the result of
                # CGParserAgent.
                # or it can be the result of MakeCallGraphAgent.
                child_fn_info = callee_info_dict[callee.create_tag()]
            elif callee.create_tag() in backup_callee_info_dict:
                child_fn_info = backup_callee_info_dict[callee.create_tag()]
            else:
                child_fn_info = callee.to_func_info()

            children.append(child_fn_info)
        cg_root_node.children = children

    def _update_children_after(
        self,
        cg_root_node: FuncInfo,
        callee_tag: str,
        callee_info_or_cg_node: FuncInfo,
    ) -> None:
        """Update the children of the call graph root node."""
        for idx, callee in enumerate(cg_root_node.children):
            # callee is the callee info we got from caller function.
            if callee.create_tag() == callee_tag:
                cg_root_node.children[idx] = callee_info_or_cg_node
                break

    def _update_interest_info(self, cg_root_node: FuncInfo) -> None:
        """Update the interest info of the call graph root node."""
        interest_info = InterestInfo(
            is_interesting=False,
        )

        if (
            self.parent_fn
            and self.parent_fn.interest_info
            and self.parent_fn.interest_info.is_interesting
        ):
            interest_info.is_interesting = True

        if not cg_root_node.func_location.file_path:
            cg_root_node.interest_info = interest_info
            return

        diffs = self.gc.function_diffs
        if cg_root_node.func_location.file_path not in diffs:
            cg_root_node.interest_info = interest_info
            return

        diffs_in_range = extract_diffs_in_range(
            diffs[cg_root_node.func_location.file_path],
            cg_root_node.func_location.start_line,
            cg_root_node.func_location.end_line,
            cg_root_node.func_location.file_path,
            set_cg_included=True,
        )

        diff_str = accumulate_diffs(
            diffs_in_range,
            in_mcga=True,
        )

        if diff_str:
            interest_info.is_interesting = True
            interest_info.diff = diff_str
            logger.info(f"fn name: {cg_root_node.func_location.func_name}")

        cg_root_node.interest_info = interest_info

    def _check_if_node_done(self, node: FuncInfo) -> bool:
        """Check if the node is done."""
        if not is_valid_callee_funcinfo(node):
            return True

        tag = make_mcga_cache_tag(
            node,
            self.gc.cp.name,
            self.gc.cur_harness.name,
        )
        res = self.gc.redis.get(tag)

        if res is None:
            return False

        mcga_func_info = MCGAFuncInfo.model_validate_json(res)
        return mcga_func_info.done

    def _record_cg_root_node(self, cg_root_node: FuncInfo, done: bool = False) -> None:

        if done and not self.parent_fn:
            logger.info(
                f"[MCGA] {self.target_fn[0]} done. Add sentinel to priority queue."
            )
            self.priority_queue.put_nowait(
                (100000000000000, 100000000000000, 100000000000000, None)
            )

        mcga_func_info = MCGAFuncInfo(
            done=done,
            in_run=True,
            **cg_root_node.model_dump(),
        )

        cgs_json = self._serialize_cg_root_node(mcga_func_info)

        with open(self.ret_file, "w") as f:
            f.write(cgs_json)

        tag = make_mcga_cache_tag(
            cg_root_node,
            self.gc.cp.name,
            self.gc.cur_harness.name,
        )
        res = self.gc.redis.get(tag)

        if res and not done:
            pass
        else:
            self.gc.redis.set(tag, mcga_func_info.model_dump_json())

    def finalize(self, state: MakeCallGraphOverallState) -> MakeCallGraphOutputState:

        cg_root_node = state["cg_root_node"]
        done = state["done"]

        # mark DONE only when the cg_root_node was from cache.
        if cg_root_node and done:
            # logger.info(f"🟢 {cg_root_node.func_location.func_name} (done: True)")
            self._record_cg_root_node(cg_root_node, done=True)

        return state
