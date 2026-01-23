import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import multilspy.multilspy_types as multilspy_types
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import MessagesState, add_messages
from loguru import logger
from mljoern.client import JoernClient
from multilspy.language_server import LanguageServer
from typing_extensions import Annotated

from mlla.utils.agent import TOOL_MODEL, BaseAgentTemplate
from mlla.utils.execute_llm_code import collect_code_block

from ..codeindexer.codeindexer import CodeIndexer
from ..codeindexer.parser import CIFunctionRes
from ..prompts.cgparser import (
    SEARCH_RESULTS_FORMAT,
    SEARCH_RESULTS_FORMAT_WITH_SIGNATURE,
    SELECT_CODE_DICT_HUMAN,
    SELECT_CODE_DICT_SYSTEM,
)
from ..prompts.cpua import CPUA_ERROR
from ..utils import find_string_in_file, normalize_func_name, normalize_func_name_for_ci
from ..utils.cg import FuncInfo
from ..utils.context import GlobalContext
from ..utils.joern_adaptor import query_joern
from ..utils.llm_tools.astgrep import AGTool
from ..utils.state import merge_with_update


async def _get_callsite_location(
    lsp_server: LanguageServer,
    positions: list[tuple[int, int]],
    caller_file_path: str,
    fn_name: str,
) -> Optional[tuple[int, int]]:
    for pos in positions:
        new_pos = (
            pos[0] - 1,
            pos[1],  # give one more column number
        )
        if await check_callsite_location(
            lsp_server, caller_file_path, new_pos, fn_name
        ):
            return new_pos
    return None


async def adjust_callsite_location(
    lsp_server: LanguageServer,
    caller_file_path: str,
    callsite_location: Optional[tuple[Optional[int], Optional[int]]],
    callsite_range: Optional[tuple[int, int]],
    fn_name: str,
) -> Optional[tuple[int, int]]:
    positions = find_string_in_file(caller_file_path, fn_name)

    def _filter_fn(x: tuple[int, int]) -> bool:
        if callsite_range:
            return x[0] >= callsite_range[0] and x[0] <= callsite_range[1]
        return True

    positions = list(filter(_filter_fn, positions))

    if not callsite_location:
        return await _get_callsite_location(
            lsp_server, positions, caller_file_path, fn_name
        )
    elif callsite_location[0] and not callsite_location[1]:

        def _filter_fn(x: tuple[int, int]) -> bool:
            return x[0] == callsite_location[0]

        positions = list(filter(_filter_fn, positions))
        return await _get_callsite_location(
            lsp_server, positions, caller_file_path, fn_name
        )
    elif callsite_location[0] and callsite_location[1]:
        new_callsite_location = (
            callsite_location[0] - 1,
            callsite_location[1],  # give one more column number
        )
        if await check_callsite_location(
            lsp_server, caller_file_path, new_callsite_location, fn_name
        ):
            return new_callsite_location
        else:
            return await _get_callsite_location(
                lsp_server, positions, caller_file_path, fn_name
            )

    else:
        logger.warning(f"Weird case: {caller_file_path}, {callsite_location}")
        return await _get_callsite_location(
            lsp_server, positions, caller_file_path, fn_name
        )


async def check_callsite_location(
    lsp_server: LanguageServer,
    caller_file_path: str,
    callsite_location: tuple[int, int],
    fn_name: str,
) -> bool:
    try:
        lsp_results = await asyncio.wait_for(
            lsp_server.request_hover(
                caller_file_path,
                callsite_location[0],
                callsite_location[1],
            ),
            timeout=30,
        )
    except asyncio.TimeoutError:
        logger.debug(f"Timeout for hover: {fn_name}")
        return False
    except Exception:
        return False

    if not lsp_results:
        # logger.warning(
        #     f"hover result is empty for {fn_name}: {caller_file_path},"
        #     f" {callsite_location}"
        # )
        return False

    if "contents" not in lsp_results:
        logger.warning(f"No contents. hover for {fn_name}: {lsp_results}")
        return False

    contents = lsp_results["contents"]

    str_contents = str(contents)

    if fn_name in str_contents:
        return True

    # logger.warning(f"No {fn_name} in hover result {lsp_results}
    # @ {callsite_location}")
    return False


async def _search_with_code_indexer(
    code_indexer: CodeIndexer,
    fn_name: str,
) -> list[CIFunctionRes]:
    """Search for function definition using CodeIndexer"""
    return await code_indexer.search_function(fn_name)


async def _search_with_agtool(
    fn_name: str,
    callee_file_path: str,
) -> list[CIFunctionRes]:
    """Search for function definition using AGTool"""
    agtool = AGTool()
    try:
        ag_results = agtool.search_function_definition(fn_name, callee_file_path)
        return [r.to_cifunctionres() for r in ag_results if r.name == fn_name]
    except Exception:
        # logger.warning(f"Error searching for function {fn_name}: {e}")
        return []


async def lsp_res_to_funcinfos(
    code_indexer: CodeIndexer,
    lsp_server: LanguageServer,
    lsp_res: list[multilspy_types.Location],
    fn_name: str,
) -> list[FuncInfo]:
    fi_res = []

    for loc_res in lsp_res:
        callee_file_path = loc_res["absolutePath"]
        search_results = None
        if Path(callee_file_path).exists():
            try:
                lsp_sym_results, _ = await asyncio.wait_for(
                    lsp_server.request_document_symbols(
                        callee_file_path,
                    ),
                    timeout=60,
                )
            except Exception as e:
                logger.warning(f"Error getting LSP symbols: {e}")
                lsp_sym_results = []

            lsp_sym_results = list(
                filter(
                    lambda x: normalize_func_name(x["name"]) == fn_name
                    and x["selectionRange"] == loc_res["range"],
                    lsp_sym_results,
                )
            )

            if len(lsp_sym_results) == 1:
                # logger.info(f"lsp_sym_results: {lsp_sym_results}")
                fi_res.append(FuncInfo.from_lsp_res(loc_res, lsp_sym_results[0]))
                continue

            search_results = await _search_with_agtool(fn_name, callee_file_path)
            if search_results:
                search_results = list(
                    filter(
                        lambda x: x.start_line - 1 == loc_res["range"]["start"]["line"],
                        search_results,
                    )
                )
                if search_results:
                    fi_res.append(FuncInfo.from_ci_res(search_results[0]))
        if not search_results:
            search_results = await _search_with_code_indexer(code_indexer, fn_name)
            if search_results:
                search_results = list(
                    filter(
                        lambda x: x.start_line - 1 == loc_res["range"]["start"]["line"],
                        search_results,
                    )
                )
                if search_results:
                    fi_res.append(FuncInfo.from_ci_res(search_results[0]))

    return fi_res


async def _search_with_lsp(
    gc: GlobalContext,
    caller_file_path: str,
    callsite_location: Optional[tuple[Optional[int], Optional[int]]],
    callsite_range: Optional[tuple[int, int]],
    fn_name: str,
) -> list[FuncInfo]:
    """Search for function definition using LSP server"""
    lsp_loc_results = []

    new_callsite_location = await adjust_callsite_location(
        gc.lsp_server, caller_file_path, callsite_location, callsite_range, fn_name
    )

    if not new_callsite_location:
        if gc.cp.language == "jvm" and caller_file_path in map(
            lambda x: x.src_path.as_posix(), gc.cp.harnesses.values()
        ):
            pass
        else:
            logger.debug(
                f"Failed to adjust caller location for {fn_name}: {caller_file_path},"
                f" {callsite_location}"
            )
        return []

    try:
        lsp_loc_results = await asyncio.wait_for(
            gc.lsp_server.request_definition(
                caller_file_path,
                new_callsite_location[0],
                new_callsite_location[1],
            ),
            timeout=60,
        )

    except Exception as e:
        logger.warning(f"Error searching for function {fn_name}: {e}")

    if gc.cp.language == "jvm":
        try:
            extra_lsp_results = await asyncio.wait_for(
                gc.lsp_server.request_declaration(
                    caller_file_path,
                    new_callsite_location[0],
                    new_callsite_location[1],
                ),
                timeout=60,
            )
            lsp_loc_results.extend(extra_lsp_results)
        except Exception as e:
            logger.warning(f"Error searching for function {fn_name}: {e}")

    def _filter_fn(x: multilspy_types.Location) -> bool:
        # filter out the result that is the same as the caller file path and the
        # caller location
        return not (
            x["absolutePath"] == caller_file_path
            and x["range"]["start"]["line"] == new_callsite_location[0]
            and (
                x["range"]["start"]["character"] <= new_callsite_location[1]
                and x["range"]["end"]["character"] >= new_callsite_location[1]
            )
        )

    lsp_loc_results = list(
        filter(
            _filter_fn,
            lsp_loc_results,
        )
    )

    funcinfos = await lsp_res_to_funcinfos(
        gc.code_indexer, gc.lsp_server, lsp_loc_results, fn_name
    )
    return funcinfos


def dedup_fn_infos(
    fn_infos: list[FuncInfo], filter_body: bool = False
) -> list[FuncInfo]:
    """Deduplicate function information"""
    d = {}
    if not filter_body:
        filter_body = True if len(fn_infos) > 1 else False
    for fn_info in fn_infos:
        if fn_info.func_body:
            if filter_body:
                if "{" in fn_info.func_body:
                    tag = fn_info.create_tag()
                    d[tag] = fn_info
            else:
                tag = fn_info.create_tag()
                d[tag] = fn_info
        else:
            tag = fn_info.create_tag()
            d[tag] = fn_info

    return list(d.values())


async def joern_method_query_to_funcinfo(
    joern_client: JoernClient,
    joern_lock: asyncio.Lock,
    query: str,
    proj_path: str,
    error_msg_when_no_results: str,
) -> tuple[bool, list[FuncInfo]]:
    """Convert Joern method query to function information
    Returns:
        - bool: True if the results are decided (len(results) == 1 or 0), False
        otherwise
        - list[FuncInfo]: The function information
    """

    def _filter_fn(x) -> bool:
        if not isinstance(x, dict):
            logger.warning(f"Joern result is not a dict: {x}")
            # import pdb; pdb.set_trace()
            return False
        if x.get("code", "<empty>") == "<empty>":
            return False
        if x.get("isExternal", True):
            return False
        return True

    results_any = await query_joern(joern_client, query, joern_lock)
    if isinstance(results_any, list):
        results = list(
            filter(
                _filter_fn,
                results_any,
            )
        )
    elif results_any is None:
        results = []
    else:
        logger.warning(f"Joern query {query} returned {results_any}")
        results = []
    # logger.debug(f"Joern query: {query}")
    # logger.debug(f"Joern results: {results}")

    res = _check_joern_results(results, error_msg_when_no_results)

    return res, [FuncInfo.from_joern_method(r, base_path=proj_path) for r in results]


def _check_joern_results(results: list[dict], msg: str) -> bool:
    """Check if the results are valid"""
    if len(results) == 0:
        logger.debug(msg)
        return True
    if len(results) == 1:
        return True
    return False


async def _search_with_joern_with_callee_info(
    joern_client: JoernClient,
    joern_lock: asyncio.Lock,
    fn_name: str,
    callee_file_path: Optional[str],
    proj_path: str,
) -> list[FuncInfo]:
    """Search for function definition using Joern with callee information"""
    callee_query = f'cpg.method.name("{fn_name}").distinct'

    res, callee_results = await joern_method_query_to_funcinfo(
        joern_client,
        joern_lock,
        callee_query,
        proj_path,
        f"No results found for function {fn_name} in Joern.",
    )

    if res:
        return callee_results

    if callee_file_path:
        try:
            relative_callee_file_path = Path(callee_file_path).relative_to(proj_path)
        except Exception as e:
            import traceback

            logger.warning(f"Error getting relative path for {callee_file_path}: {e}")
            tb_lines = traceback.format_exc()
            logger.warning(tb_lines)
            relative_callee_file_path = Path(callee_file_path)

        callee_query = f"""cpg.method.name("{fn_name}")
        .filename("{relative_callee_file_path}")
        .distinct"""
        res, callee_results_with_file_path = await joern_method_query_to_funcinfo(
            joern_client,
            joern_lock,
            callee_query,
            proj_path,
            f"No results found for function {fn_name} @ callee: {callee_file_path} in"
            " Joern.",
        )
        if res:
            return callee_results_with_file_path

        callee_results = callee_results + callee_results_with_file_path

    # if not res:
    # logger.warning(f"Multiple candidates found for function {fn_name} in Joern.")
    # for r in callee_results:
    #     logger.debug(f" - {r}")

    return callee_results


async def _search_with_joern(
    joern_client: JoernClient,
    joern_lock: asyncio.Lock,
    fn_name: str,
    callee_file_path: Optional[str],
    caller_file_path: Optional[str],
    callsite_location: Optional[tuple[Optional[int], Optional[int]]],
    callsite_range: Optional[tuple[int, int]],
    proj_path: str,
) -> list[FuncInfo]:
    """Search for function definition using Joern"""
    callee_results = await _search_with_joern_with_callee_info(
        joern_client, joern_lock, fn_name, callee_file_path, proj_path
    )

    if len(callee_results) == 1 or not caller_file_path:
        return callee_results

    try:
        relative_caller_file_path = Path(caller_file_path).relative_to(proj_path)
    except Exception:
        return callee_results

    caller_query = f"""cpg.method.name("{fn_name}")
    .callIn.method.filename("{relative_caller_file_path}")
    .callee.name("{fn_name}")
    .distinct"""

    res, caller_results = await joern_method_query_to_funcinfo(
        joern_client,
        joern_lock,
        caller_query,
        proj_path,
        f"No results found for function {fn_name} @ caller: {caller_file_path} in"
        " Joern.",
    )

    if res:
        return caller_results

    if callsite_location and callsite_location[0]:
        start_line = callsite_location[0]
        caller_query = f"""cpg.method.name("{fn_name}")
        .callIn.lineNumber({start_line})
        .method.filename("{relative_caller_file_path}")
        .callee.name("{fn_name}")
        .distinct"""

        res, caller_results = await joern_method_query_to_funcinfo(
            joern_client,
            joern_lock,
            caller_query,
            proj_path,
            f"No results found for function {fn_name} @"
            f" {caller_file_path}:{start_line} in Joern.",
        )

        if res:
            return caller_results

    if callsite_range:
        start_line = callsite_range[0]
        end_line = callsite_range[1]
        caller_query = f"""cpg.method.name("{fn_name}").callIn
        .filter(call => call.lineNumber.l.exists(line => line >= {start_line}
        && line <= {end_line}))
        .method.filename("{relative_caller_file_path}")
        .callee.name("{fn_name}")
        .distinct"""

        res, caller_results = await joern_method_query_to_funcinfo(
            joern_client,
            joern_lock,
            caller_query,
            proj_path,
            f"No results found for function {fn_name} @"
            f" {caller_file_path}:{start_line}-{end_line} in Joern.",
        )

        if res:
            return caller_results

    result = callee_results + caller_results
    return result


async def get_fn_search_results(
    gc: GlobalContext,
    fn_name: str,
    callee_file_path: Optional[str] = None,
    caller_file_path: Optional[str] = None,
    callsite_location: Optional[tuple[Optional[int], Optional[int]]] = None,
    callsite_range: Optional[tuple[int, int]] = None,
) -> list[FuncInfo]:
    """Search for function in lsp, code indexer and AGTool"""
    ag_search_results = []
    lsp_search_results = []
    joern_search_results = []
    ci_search_results = []

    lsp_search_results_task = None
    joern_search_results_task = None
    ci_search_results_task = None

    if callee_file_path:
        search_ag_results = await _search_with_agtool(
            normalize_func_name(fn_name), callee_file_path
        )
        if search_ag_results:
            # logger.debug(f"Function {fn_name} found in AGTool")
            ag_search_results = [FuncInfo.from_ci_res(r) for r in search_ag_results]
        else:
            logger.debug(f"No results found for function {fn_name} in AGTool")

    if ag_search_results:
        ag_search_results = dedup_fn_infos(ag_search_results)
        logger.info(f"Function {fn_name} found in AGTool.")
        if len(ag_search_results) == 1:
            return ag_search_results

        logger.debug(f"Function {fn_name} found in AGTool: {len(ag_search_results)}")

    # Try LSP search if caller information is available
    if caller_file_path and gc.lsp_server:
        # if inited:
        lsp_search_results_task = asyncio.create_task(
            _search_with_lsp(
                gc,
                caller_file_path,
                callsite_location,
                callsite_range,
                normalize_func_name_for_ci(fn_name),
            )
        )

    if gc.joern_client:
        joern_search_results_task = asyncio.create_task(
            _search_with_joern(
                gc.joern_client,
                gc.joern_lock,
                normalize_func_name(fn_name),
                callee_file_path,
                caller_file_path,
                callsite_location,
                callsite_range,
                gc.cp.proj_path.as_posix(),
            )
        )

    if gc.code_indexer:
        ci_search_results_task = asyncio.create_task(
            _search_with_code_indexer(
                gc.code_indexer, normalize_func_name_for_ci(fn_name)
            )
        )

    if ci_search_results_task:
        try:
            ci_search_direct_results = await asyncio.wait_for(
                ci_search_results_task, timeout=30
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout for function {fn_name} in CodeIndexer")
            ci_search_direct_results = []

        ci_search_results = [FuncInfo.from_ci_res(r) for r in ci_search_direct_results]
        if ci_search_results:
            ci_search_results = dedup_fn_infos(ci_search_results)
            logger.debug(
                f"Function {fn_name} found in CodeIndexer: {len(ci_search_results)}"
            )

    if joern_search_results_task:
        try:
            joern_search_results = await asyncio.wait_for(
                joern_search_results_task, timeout=30
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout for function {fn_name} in Joern")
            joern_search_results = []

        if joern_search_results:
            joern_search_results = dedup_fn_infos(joern_search_results)
            logger.debug(
                f"Function {fn_name} found in Joern: {len(joern_search_results)}"
            )

    if lsp_search_results_task:
        try:
            lsp_search_results = await asyncio.wait_for(
                lsp_search_results_task, timeout=180
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout for function {fn_name} in LSP")
            lsp_search_results = []

        if lsp_search_results:
            lsp_search_results = dedup_fn_infos(lsp_search_results)
            logger.debug(f"Function {fn_name} found in LSP: {len(lsp_search_results)}")

    # search_results should be the list among ag_search_results,
    # lsp_search_results, joern_search_results, and ci_search_results having the
    # least number of results, but not empty
    search_results = None
    min_results = float("inf")

    results_dict = {
        "AGTool": dedup_fn_infos(ag_search_results, filter_body=True),
        "LSP": dedup_fn_infos(lsp_search_results, filter_body=True),
        "Joern": dedup_fn_infos(joern_search_results, filter_body=True),
        "CodeIndexer": dedup_fn_infos(ci_search_results, filter_body=True),
    }

    if sum(len(r) for r in results_dict.values()) == 0:
        results_dict = {
            "AGTool": ag_search_results,
            "LSP": lsp_search_results,
            "Joern": joern_search_results,
            "CodeIndexer": ci_search_results,
        }

    results_name = ""
    for name, results in results_dict.items():
        if results and len(results) < min_results:
            min_results = len(results)
            search_results = results
            results_name = name

    if not search_results:
        search_results = []

    if search_results:
        logger.info(f"{fn_name}: {len(search_results)} from {results_name}")
        logger.debug(f"callee_file_path: {callee_file_path}")
        logger.debug(f"caller_file_path: {caller_file_path}")
        logger.debug(f"callsite_location: {callsite_location}")
        logger.debug(f"callsite_range: {callsite_range}")
        if len(search_results) == 1:
            logger.info(f"   - {search_results[0].func_location.file_path}")
            logger.debug(f"   - {search_results[0].func_signature}")
            logger.debug(
                f"   - {search_results[0].func_body}\n"
                "============================================\n"
            )

    return search_results


def _get_import_statements(file_path: str) -> str:
    """Get import statements from file"""
    content = ""
    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    import_statements = ""
    for line in content.splitlines():
        # Java
        if line.strip().startswith("import"):
            import_statements += line.strip() + "\n"
        # C / C++
        elif line.strip().startswith("#include"):
            import_statements += line.strip() + "\n"

    return import_statements


def _get_invoke_code(
    file_path: str,
    func_name: str,
    caller_fn_body: Optional[str],
    callsite_location: Optional[tuple[int, int]],
    callsite_range: Optional[tuple[int, int]],
) -> str:
    """Get invoke code from file"""
    content = ""
    if caller_fn_body:
        content = caller_fn_body
    else:
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

    invoke_code = ""

    positions = find_string_in_file(file_path, normalize_func_name_for_ci(func_name))

    if callsite_location:
        positions = [
            pos
            for pos in positions
            if callsite_location[0] - 2 <= pos[0] <= callsite_location[0] + 2
        ]

    elif callsite_range:
        positions = [
            pos
            for pos in positions
            if pos[0] >= callsite_range[0] and pos[0] <= callsite_range[1]
        ]

    prev_line_number = -100

    LINE_GAP = 25

    for pos in positions:
        line_number = pos[0]
        if line_number <= prev_line_number + LINE_GAP:
            continue
        start_line_number = max(0, line_number - LINE_GAP)
        end_line_number = min(len(content.splitlines()) - 1, line_number + 5)
        for line in content.splitlines()[start_line_number:end_line_number]:
            invoke_code += line.strip() + "\n"
        invoke_code += "============================================\n"
        prev_line_number = line_number

    if not invoke_code and caller_fn_body:
        invoke_code = caller_fn_body

    return invoke_code


def _select_code_dict_verifier(
    response: BaseMessage, search_results: list[FuncInfo]
) -> Optional[FuncInfo]:
    """Select appropriate function definition from search results"""
    parser = JsonOutputParser()

    try:
        json_str = collect_code_block(response.content, lang="json")
        if not json_str:
            json_str = [response.content]
        res_dict = parser.invoke(json_str[0])
    except Exception as e:
        raise Exception(f"response content is not valid json: {e}")

    selected_idx = int(res_dict["selected_idx"])

    if selected_idx < 0:
        return None

    if selected_idx >= len(search_results):
        raise Exception(f"selected_idx is out of range: {selected_idx}")

    return search_results[selected_idx]


class CGParserInputState(MessagesState):
    fn_name: str
    fn_file_path: Annotated[Optional[str], merge_with_update]
    caller_file_path: Annotated[Optional[str], merge_with_update]
    caller_fn_body: Annotated[Optional[str], merge_with_update]
    callsite_location: Annotated[
        Optional[tuple[int, int]], merge_with_update
    ]  # 1-indexed (line, column);
    # If you know the exact location that the function is called, you can provide it.
    callsite_range: Annotated[
        Optional[tuple[int, int]], merge_with_update
    ]  # 1-indexed (start_line, end_line);
    # If you know the range that the function is called, you can provide it.


class CGParserOutputState(MessagesState):
    code_dict: Annotated[Optional[FuncInfo], merge_with_update]


class CGParserOverallState(CGParserInputState, CGParserOutputState):
    step: Annotated[int, merge_with_update]
    search_results: Annotated[list[FuncInfo], merge_with_update]


def cgpa_create_tag(state: CGParserOverallState) -> str:
    tag = state.get("fn_name", "")
    if state.get("fn_file_path", None):
        tag += f":{state['fn_file_path']}"
    if state.get("caller_file_path", None):
        tag += f":{state['caller_file_path']}"
    if state.get("callsite_location", None):
        tag += f":{state['callsite_location'][0]}:{state['callsite_location'][1]}"
    if state.get("callsite_range", None):
        tag += f":{state['callsite_range'][0]}:{state['callsite_range'][1]}"

    return tag


class CGParserAgent(BaseAgentTemplate):
    code_indexer: CodeIndexer

    def __init__(self, config: GlobalContext, no_llm: bool = False):
        ret_dir = config.RESULT_DIR / "cgparser_agent"
        super().__init__(
            config,
            ret_dir,
            CGParserInputState,
            CGParserOutputState,
            CGParserOverallState,
            step_mapper={
                # 1: "get_code_dict_from_fn",
                2: "select_code_dict",
            },
            enable_usage_snapshot=False,
            llm_with_tools=os.getenv("CGPA_MODEL", "o4-mini"),
        )

        self.builder.add_node("get_code_dict_from_fn", self.get_code_dict_from_fn)
        self.builder.add_node("select_code_dict", self.select_code_dict)

        self.builder.add_conditional_edges(
            "preprocess",
            lambda state: (
                "finalize" if "code_dict" in state else "get_code_dict_from_fn"
            ),
            ["finalize", "get_code_dict_from_fn"],
        )
        self.builder.add_edge("get_code_dict_from_fn", "select_code_dict")
        if not no_llm:
            self.builder.add_conditional_edges(
                "select_code_dict",
                lambda state: ("finalize" if "code_dict" in state else TOOL_MODEL),
                ["finalize", TOOL_MODEL],
            )
        else:
            self.builder.add_edge("select_code_dict", "finalize")

    def preprocess(self, state: CGParserInputState) -> CGParserOverallState:
        fn_name = state["fn_name"]
        real_fn_name = fn_name

        code_dict = None
        tag = f"cgpa::{self.gc.cp.name}::" + cgpa_create_tag(state)
        code_dict_str = self.gc.redis.get(tag)
        if code_dict_str:
            logger.debug(f"[CGPA] Redis hit for {real_fn_name}")
            try:
                code_dict = FuncInfo.model_validate_json(code_dict_str)
            except Exception as e:
                if code_dict_str != b"None":
                    logger.warning(
                        f"[CGPA] Redis hit for {real_fn_name}, but invalid json: {e}"
                    )
                code_dict = None

            return CGParserOverallState(
                fn_name=real_fn_name,
                fn_file_path=state.get("fn_file_path", None),
                caller_file_path=state.get("caller_file_path", None),
                caller_fn_body=state.get("caller_fn_body", None),
                callsite_location=state.get("callsite_location", None),
                callsite_range=state.get("callsite_range", None),
                search_results=[],
                step=0,
                code_dict=code_dict,
            )
        else:
            return CGParserOverallState(
                fn_name=real_fn_name,
                fn_file_path=state.get("fn_file_path", None),
                caller_file_path=state.get("caller_file_path", None),
                caller_fn_body=state.get("caller_fn_body", None),
                callsite_location=state.get("callsite_location", None),
                callsite_range=state.get("callsite_range", None),
                search_results=[],
                step=0,
            )

    def finalize(self, state: CGParserOverallState) -> CGParserOutputState:
        code_dict = state["code_dict"] if "code_dict" in state else None
        tag = f"cgpa::{self.gc.cp.name}::" + cgpa_create_tag(state)
        if code_dict:
            self.gc.redis.set(tag, code_dict.model_dump_json())
        else:
            self.gc.redis.set(tag, "None")
        return state

    def deserialize(self, state, content: str) -> dict:
        return json.loads(content)

    def serialize(self, state) -> str:
        return json.dumps(state)

    async def get_code_dict_from_fn(
        self,
        state: CGParserInputState,
    ) -> CGParserOverallState:
        """Get code dictionary from function name and file path"""

        fn_name = state["fn_name"]
        fn_file_path = state.get("fn_file_path", None)
        caller_file_path = state.get("caller_file_path", None)
        caller_fn_body = state.get("caller_fn_body", None)
        callsite_location = state.get("callsite_location", None)
        callsite_range = state.get("callsite_range", None)

        search_results = await get_fn_search_results(
            self.gc,
            fn_name,
            fn_file_path,
            caller_file_path,
            callsite_location,
            callsite_range,
        )

        for idx, r in enumerate(search_results):
            if fn_name.endswith(".<init>"):
                r.func_location.func_name = (
                    normalize_func_name_for_ci(fn_name) + ".<init>"
                )
            elif fn_name.endswith(".<clinit>"):
                r.func_location.func_name = (
                    normalize_func_name_for_ci(fn_name) + ".<clinit>"
                )
            else:
                r.func_location.func_name = normalize_func_name(
                    r.func_location.func_name
                )
            # if idx == 0:
            #     logger.info(f"[CGPA] {r.func_location.func_name} {r.func_signature}")

        return CGParserOverallState(
            fn_name=fn_name,
            fn_file_path=fn_file_path,
            caller_file_path=caller_file_path,
            caller_fn_body=caller_fn_body,
            search_results=search_results,
            step=1,
        )

    def select_code_dict(
        self,
        state: CGParserOverallState,
    ) -> CGParserOverallState | CGParserOutputState:
        """Select appropriate function definition from search results"""

        fn_name = state["fn_name"]
        fn_file_path = state["fn_file_path"]
        caller_file_path = state["caller_file_path"]
        caller_fn_body = state["caller_fn_body"]
        callsite_location = state["callsite_location"]
        callsite_range = state["callsite_range"]
        search_results = state["search_results"]

        if fn_file_path:
            filtered_results = [
                r for r in search_results if r.func_location.file_path == fn_file_path
            ]
        else:
            filtered_results = search_results

        results = filtered_results

        if len(filtered_results) == 0:
            files = [result.func_location.file_path for result in search_results]
            if len(files) > 1:
                pass
                # logger.warning(
                #     f"Function {fn_name} exists, but node.file_path={fn_file_path}."
                #     f" \nFound in {files}."
                # )

            results = search_results

        if len(results) == 1:
            return CGParserOutputState(code_dict=results[0])

        if len(results) == 0:
            logger.warning(f"[CGPA] No code dict selected for {fn_name}. Empty results")
            return CGParserOutputState(code_dict=None)

        if state["step"] == 2:
            message = state["messages"][-1]
            try:
                code_dict = _select_code_dict_verifier(message, results)
            except Exception as e:
                logger.error(f"Error: {e}")

                msg = CPUA_ERROR.format(error=e)
                state["messages"] = add_messages(state["messages"], [HumanMessage(msg)])
                return state

            if code_dict is None:
                logger.warning(f"[CGPA] No code dict selected for `{fn_name}`.")
                # logger.warning(f"- fn_file_path: {fn_file_path}")
                # logger.warning(f"- caller_file_path: {caller_file_path}")
                # logger.warning(f"- callsite_location: {callsite_location}")
                # logger.warning(f"- callsite_range: {callsite_range}")
                # logger.warning(f"- caller_fn_body: {caller_fn_body}")
            else:
                logger.debug(f"   - {code_dict.func_location.file_path}")
                logger.debug(f"   - {code_dict.func_signature}")
                logger.debug(
                    f"   - {code_dict.func_body}\n"
                    "============================================\n"
                )
                # for r in results:
                #     logger.warning(str(r))

            return CGParserOutputState(code_dict=code_dict)

        import_statements = ""
        invoke_code = ""

        if not caller_file_path:
            if caller_fn_body:
                invoke_code = caller_fn_body
            elif len(results) > 0:
                # XXX: random select one
                return CGParserOutputState(code_dict=results[0])
            else:
                return CGParserOutputState(code_dict=None)

        else:
            import_statements = _get_import_statements(caller_file_path)
            if caller_fn_body:
                if len(caller_fn_body.splitlines()) > 50:
                    invoke_code = _get_invoke_code(
                        caller_file_path,
                        fn_name,
                        caller_fn_body,
                        callsite_location,
                        callsite_range,
                    )
                else:
                    invoke_code = caller_fn_body
            else:
                invoke_code = _get_invoke_code(
                    caller_file_path, fn_name, None, callsite_location, callsite_range
                )

        search_results_str = ""
        for idx, result in enumerate(results):
            if result.func_signature:
                search_results_str += SEARCH_RESULTS_FORMAT_WITH_SIGNATURE.format(
                    idx=idx,
                    func_name=result.func_location.func_name,
                    func_signature=result.func_signature,
                    file_path=result.func_location.file_path,
                    func_body=result.func_body,
                )
            else:
                search_results_str += SEARCH_RESULTS_FORMAT.format(
                    idx=idx,
                    func_name=result.func_location.func_name,
                    file_path=result.func_location.file_path,
                    func_body=result.func_body,
                )

        messages = [
            SystemMessage(content=SELECT_CODE_DICT_SYSTEM),
            HumanMessage(
                content=SELECT_CODE_DICT_HUMAN.format(
                    func_name=fn_name,
                    import_statements=import_statements,
                    invoke_code=invoke_code,
                    search_results_str=search_results_str,
                )
            ),
        ]

        return CGParserOverallState(
            messages=messages,
            fn_name=fn_name,
            fn_file_path=fn_file_path,
            caller_file_path=caller_file_path,
            search_results=search_results,
            step=2,
        )
