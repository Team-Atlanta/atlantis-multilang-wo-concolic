from pathlib import Path
from typing import List

from pydantic import BaseModel

from mlla.agents.bcda_experimental import BugCandDetectAgentOutputState
from mlla.agents.cpua import CPUnderstandAgentOutputState
from mlla.modules.sanitizer import Sanitizer
from mlla.utils import normalize_func_name
from mlla.utils.cg import FuncInfo


class CPUAResult(BaseModel):
    total: int
    reached: int
    detected: bool


class BCDAResult(BaseModel):
    detected: bool
    sanitizer_detected: bool
    total: int
    hit: int


class CPVResult(BaseModel):
    sanitizer_name: str
    cpua_res: CPUAResult
    bcda_res: BCDAResult
    exploited: bool


class CPVInfo(BaseModel):
    sanitizer_name: str
    functions_in_call_stack: List[
        tuple[str, str, int | None]
    ]  # function_path, file_name, line_number


def _get_cpv_info_java(log_path: Path) -> CPVInfo | None:
    with open(log_path, "r") as f:
        log_content = f.read()

    # Parse the log content to get the functions in the call stack
    functions_in_call_stack = []
    _functions_in_call_stack1 = []
    _functions_in_call_stack2 = []
    for line in log_content.split("\n"):
        line = line.strip()
        if line.startswith("at "):
            # Extract function, file and line number
            parts = line.split("at ")[1].split("(")
            function_path = parts[0].strip()
            location = parts[1].rstrip(")")

            # Split into file and line number
            file_line = location.split(":")
            file_name = file_line[0]
            line_number = int(file_line[1]) if len(file_line) > 1 else None
            if (
                "jazzer" not in function_path
                and "java.base/" not in function_path
                and "jaz.Zer" not in function_path
                and "harnesses" not in function_path
                # and "com.aixcc" not in function_path
            ):
                _functions_in_call_stack1.append(
                    (function_path, file_name, line_number)
                )
            if (
                "jazzer" not in function_path
                and "java.base/" not in function_path
                and "jaz.Zer" not in function_path
                # and "harnesses" not in function_path
                # and "com.aixcc" not in function_path
            ):
                _functions_in_call_stack2.append(
                    (function_path, file_name, line_number)
                )

    _, sanitizer_name = Sanitizer.detect_crash_type(log_content)

    if sanitizer_name is None:
        return None

    if len(_functions_in_call_stack1) > 0:
        functions_in_call_stack = _functions_in_call_stack1
    else:
        functions_in_call_stack = _functions_in_call_stack2

    return CPVInfo(
        sanitizer_name=sanitizer_name,
        functions_in_call_stack=functions_in_call_stack,
    )


def get_cpv_info(cpv_name: str, log_path: Path) -> CPVInfo | None:
    # TODO: add more cases for other languages
    return _get_cpv_info_java(log_path)


def get_cpv_res(
    cpv_id: str,
    log_path: Path,
    cpua_state: CPUnderstandAgentOutputState | None,
    bcda_state: BugCandDetectAgentOutputState | None,
    harness_name: str,
) -> CPVResult | None:

    cpv_info = get_cpv_info(cpv_id, log_path)

    # logger.warning(f"cpv_info: {cpv_info}")

    if cpv_info is None:
        return None

    cpua_res = CPUAResult(
        total=len(cpv_info.functions_in_call_stack),
        reached=0,
        detected=False,
    )

    # last_func = cpv_info["functions_in_call_stack"][0]
    # last_func_name = last_func[0]

    funcs_from_cg = set()
    reached_fn_set = set()

    def _func(node: FuncInfo):
        funcs_from_cg.add(normalize_func_name(node.func_location.func_name))

    function_name_in_call_stack = set(
        map(lambda x: x[0], cpv_info.functions_in_call_stack)
    )

    if cpua_state is not None:
        cg_list = cpua_state["CGs"].get(harness_name, [])
        # Check if the functions in call stack are in the cpua_state
        for cg in cg_list:
            cg.call_recursive(_func)
            for func in function_name_in_call_stack:
                if normalize_func_name(cg.name) in func:
                    cpua_res.detected = True
                if (
                    normalize_func_name(func)
                ) in funcs_from_cg and func not in reached_fn_set:
                    cpua_res.reached += 1
                    reached_fn_set.add(func)

    detected = False
    sanitizer_detected = False
    total = 0
    hit = 0

    if bcda_state is not None:
        # Check if the bits in bcda_state contains the cpv_info
        found = False
        total = len(bcda_state["BITs"])
        for bit in bcda_state["BITs"]:
            for func in function_name_in_call_stack:
                if normalize_func_name(bit.func_location.func_name) in func:
                    detected = True
                    sanitizer_type = bit.san_type_to_san_name()
                    if sanitizer_type in cpv_info.sanitizer_name:
                        sanitizer_detected = True
                        hit += 1
                    found = True
                    # break
            if not found:
                # logger.info(
                #     f"func_name: {normalize_func_name(bit.func_location.func_name)}"
                # )
                # logger.info(f"call stack: {function_name_in_call_stack}")
                pass

    if hit > total:
        hit = total

    bcda_res = BCDAResult(
        detected=detected,
        sanitizer_detected=sanitizer_detected,
        total=total,
        hit=hit,
    )

    return CPVResult(
        sanitizer_name=cpv_info.sanitizer_name,
        cpua_res=cpua_res,
        bcda_res=bcda_res,
        exploited=False,
    )
