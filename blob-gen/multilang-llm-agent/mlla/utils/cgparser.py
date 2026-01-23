from loguru import logger

from ..agents.cgpa import get_fn_search_results
from ..utils import find_string_in_file, normalize_func_name, normalize_func_name_for_ci
from ..utils.context import GlobalContext
from ..utils.llm_tools.astgrep import AGTool, RetrievalResult


def verify_function_list_defined_in_harness(
    fn_lst: list[str], target_fn_name: str, harness_path: str
) -> tuple[list[list[RetrievalResult]], list[str]]:
    emsgs = []
    ag_results: list[list[RetrievalResult]] = []
    ag_tool = AGTool()

    # This is to check if the all functions in the list are defined in the harness file.
    for idx, fn_name in enumerate(fn_lst[:-1]):
        if fn_name == target_fn_name:
            emsgs.append(
                f"{fn_name} is the target API candidate, so only can be "
                "the last element in the list."
            )
            logger.warning(
                f"Skipping {fn_name} because it is the target function. "
                f"fn_list: {fn_lst}"
            )
            continue
        try:
            results = ag_tool.search_function_definition(
                normalize_func_name(fn_name), harness_path
            )
        except Exception as e:
            emsgs.append(f"Error searching definition of function {fn_name}: {e}\n")

        if not results:
            emsgs.append(
                f"{fn_name} is not defined in the harness file, but in {fn_lst}. Except"
                " the last element (target API), all functions must be defined in the"
                " harness file."
            )
            logger.warning(
                f"{fn_name} is not defined in the harness file, but in {fn_lst}. Except"
                " the last element (target API), all functions must be defined in the"
                " harness file."
            )
            # break
        else:
            ag_results.append(results)

    if len(emsgs) > 0:
        ag_results = []

    return ag_results, emsgs


def validate_function_list_invoked_in_harness(
    fn_lst: list[str],
    harness_path: str,
    ag_results: list[list[RetrievalResult]],
    is_reflection: bool = False,
) -> tuple[list[tuple[int, int]], list[str]]:
    emsgs = []
    last_positions = []

    for idx, fn_name in enumerate(fn_lst):
        real_fn_name = normalize_func_name_for_ci(fn_name)
        positions = find_string_in_file(harness_path, real_fn_name)
        if not positions:
            if is_reflection and idx == len(fn_lst) - 1:
                pass
            else:
                emsgs.append(
                    f"Function {fn_name} is not invoked in the harness file."
                    f" The function in {fn_lst} must be invoked by the harness, "
                    "so the response is wrong"
                )

        elif idx == len(fn_lst) - 1:
            last_positions = positions

        if idx > 0 and ag_results:
            ag_result = ag_results[idx - 1]
            line_numbers: list[int] = [pos[0] for pos in positions]
            if any(
                result.line_start <= line_number and result.line_end >= line_number
                for result in ag_result
                for line_number in line_numbers
            ):
                pass
            else:
                if is_reflection and idx == len(fn_lst) - 1:
                    pass
                else:
                    emsgs.append(
                        f"Function {fn_name} is not invoked in the Function"
                        f" {fn_lst[idx - 1]}."
                    )

    return last_positions, emsgs


async def validate_functions(
    fn_lst: list[str],
    harness_path: str,
    api_name: str,
    gc: GlobalContext,
    cp_src_path: str,
    is_reflection: bool = False,
) -> tuple[list[tuple[int, int]], list[str]]:

    ag_results, emsgs = verify_function_list_defined_in_harness(
        fn_lst, api_name, harness_path
    )

    last_positions, _emsgs = validate_function_list_invoked_in_harness(
        fn_lst, harness_path, ag_results, is_reflection
    )
    emsgs.extend(_emsgs)

    # Check if the api function is defined in the harness file.
    search_results = await get_fn_search_results(
        gc, api_name, caller_file_path=harness_path
    )

    # because it should not be.
    if len(search_results) > 0 and not any(
        search_result.func_location.file_path
        and search_result.func_location.file_path.startswith(cp_src_path)
        for search_result in search_results
    ):
        emsgs.append(
            f"Function {api_name} is not defined in the project ({cp_src_path})."
        )

    return last_positions, emsgs
