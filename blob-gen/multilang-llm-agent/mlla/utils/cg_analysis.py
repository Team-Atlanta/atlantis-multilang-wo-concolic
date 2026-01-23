"""This file is deprecated"""

# mypy: ignore-errors

import asyncio
from pathlib import Path

from loguru import logger
from typing_extensions import Dict, List, Optional, TypedDict, Union

from ..codeindexer.main import CodeIndexer
from ..codeindexer.parser import CIFunctionRes
from ..utils.bit import BugInducingThing
from ..utils.code_tags import CODE_TAG, END_CODE_TAG, END_SOURCE_TAG, SOURCE_TAG
from ..utils.context import GlobalContext
from ..utils.cp import sCP_Harness
from .cg import CG, FuncInfo


class CodeInfo(TypedDict):
    cg: Optional[CG]
    bit: Optional[BugInducingThing]
    cg_code: str


def insert_at_lines(
    func_body: str,
    lines_to_modify: Union[int, List[int]],  # lines should be realtive.
    insert_text: Union[str, List[str]],
    append_to_line: bool = False,
) -> str:
    """Insert or append text at specified line numbers."""
    if isinstance(lines_to_modify, int):
        lines_to_modify = [lines_to_modify]
        if isinstance(insert_text, list):
            insert_text = insert_text[0]

    # Convert single string to list of same length as lines_to_modify
    if isinstance(insert_text, str):
        insert_text = [insert_text] * len(lines_to_modify)
    elif len(insert_text) != len(lines_to_modify):
        raise ValueError("Length of insert_text must match length of lines_to_modify")

    lines = func_body.split("\n")
    added_lines = 0

    # Sort by line number to handle lines in order
    sorted_pairs = sorted(zip(lines_to_modify, insert_text))
    for line_num, text in sorted_pairs:
        if line_num < 0 or line_num + added_lines >= len(lines):
            logger.warning(f"Line number {line_num} out of range")
            continue

        if append_to_line:
            # Append to end of line
            lines[line_num + added_lines] += text
        else:
            # Insert as new line
            lines.insert(line_num + added_lines, text)
            added_lines += 1

    return "\n".join(lines)


async def search_function_with_path(
    code_indexer: CodeIndexer, func_name: str, file_path: Optional[str] = None
) -> Optional[CIFunctionRes]:
    """Search for a function by name and optionally file path."""
    func_results = await code_indexer.search_function(func_name)

    if not func_results:
        logger.warning(f"Function {func_name} not found")
        return None

    if file_path:
        # Try to find exact match first
        for func in func_results:
            if func.file_path == file_path:
                return func

        # Try to match with endswith for relative paths
        for func in func_results:
            if str(func.file_path).endswith(str(file_path)):
                return func

        logger.warning(f"Function {func_name} not found in {file_path}")
        return None

    # If no path specified, return first result
    # #262 is addressed, and this should not be reached.
    # raise ValueError(f"Function {func_name} at {file_path} does not exist")
    return func_results[0]


def make_func_prompt(name, body, path=None):
    # Add full function name as comment
    func_str = "<FUNCTION>\n"
    if path:
        func_str += f"<PATH> {path} </PATH>\n"
    func_str += f"<NAME> {name} </NAME>\n"
    func_str += "<BODY>\n"
    func_str += f"{body}\n"
    func_str += "</BODY>\n"
    func_str += "</FUNCTION>\n"

    return func_str


def make_harness_prompt(harness_src_path):
    harness_str = ""
    try:
        with open(harness_src_path, "r") as f:
            harness_code = f.read()
            harness_str = "<HARNESS>\n"
            harness_str += f"<PATH> {harness_src_path} </PATH>\n"
            harness_str += "<BODY>\n"
            harness_str += f"{harness_code}\n"
            harness_str += "</BODY>\n"
            harness_str += "</HARNESS>\n"
    except (FileNotFoundError, PermissionError) as e:
        logger.error(f"Error reading harness file {harness_src_path}: {e}")

    return harness_str


def find_nodes_in_path(cg: CG, bit: BugInducingThing) -> list[FuncInfo]:
    """Find all nodes in paths from root to buggy function."""
    # if not cg.root_node:
    #     return []

    # First find the buggy node
    stack: list[FuncInfo] = [cg.root_node]
    buggy_node: FuncInfo | None = None
    parent_map: dict[str, FuncInfo] = {}  # Store node name as key

    while stack and not buggy_node:
        node: FuncInfo = stack.pop()
        # if not node:
        #     continue

        if (
            node.func_location.func_name
            and node.func_location.func_name in bit.func_location.func_name
            and str(bit.func_location.file_path) == str(node.func_location.file_path)
        ):
            buggy_node = node
            break

        # Add all children to parent map and stack
        stack.extend(node.children)
        for child in node.children:
            if (
                child.func_location.func_name not in parent_map
            ):  # Only add if not already mapped
                parent_map[child.func_location.func_name] = node

    if not buggy_node:
        return []

    # Then find all nodes in paths from root to buggy node
    nodes_in_path: list[FuncInfo] = [buggy_node]
    current: FuncInfo = buggy_node

    # Walk up the parent chain to root
    while current.func_location.func_name in parent_map:
        current = parent_map[current.func_location.func_name]
        nodes_in_path.append(current)

    # Add all children of buggy node
    stack = [buggy_node]
    while stack:
        node = stack.pop()
        if not node or not node.func_location.func_name:
            continue

        stack.extend(node.children)
        for child in node.children:
            if child not in nodes_in_path:
                nodes_in_path.append(child)

    return nodes_in_path


async def collect_all_function_bodies(gc: GlobalContext, cg: CG) -> List[str]:
    """Collect all function bodies in the call graph."""
    function_bodies = []
    stack: list[FuncInfo] = [cg.root_node]
    while stack:
        node: FuncInfo = stack.pop()
        if not node or not node.func_location.func_name:
            continue

        # Get function body
        func_res = await search_function_with_path(
            gc.code_indexer, node.func_location.func_name, node.func_location.file_path
        )
        if func_res:
            func_str = make_func_prompt(
                func_res.func_name, func_res.func_body, func_res.file_path
            )
            function_bodies.append(func_str)

        stack.extend(node.children)

    return function_bodies


async def collect_function_bodies_with_bits(
    gc: GlobalContext,
    cg: CG,
    BITs: List[BugInducingThing],
    nodes_to_include: List[FuncInfo],
) -> List[str]:
    """Collect function bodies and mark bug locations."""
    function_bodies = []

    # Process nodes in path, using set to track processed nodes
    processed: set[tuple[str, str]] = set()  # Set of (name, file_path) tuples
    for node in nodes_to_include:
        if not node or not node.func_location.func_name:
            continue

        node_key = (node.func_location.func_name, str(node.func_location.file_path))
        if node_key in processed:
            continue
        processed.add(node_key)

        # Get function body
        func_res = await search_function_with_path(
            gc.code_indexer, node.func_location.func_name, node.func_location.file_path
        )
        if func_res:
            func_lines = func_res.func_body.split("\n")
            added_lines = 0
            for bit in BITs:
                if node.func_location.func_name in bit.func_location.func_name and str(
                    bit.func_location.file_path
                ) == str(node.func_location.file_path):
                    # Line numbers from search_function starts from 1
                    start_line = bit.func_location.start_line - func_res.start_line - 1
                    end_line = bit.func_location.end_line - func_res.start_line - 1

                    # Insert /*BUG_HERE*/ markers using the new utility
                    for line in range(start_line, end_line + 1):
                        func_lines.insert(line + added_lines, "/*BUG_HERE*/")
                        added_lines += 1

                    # LETS Merge Key condition after the integration.
                    # for key_condition in bit.key_conditions:
                    #     node_name = node.func_location.func_name
                    #     bit_name = bit.func_location.func_name
                    #     if node_name in bit_name and
                    #        str(bit.func_location.file_path) == str(
                    # node.func_location.file_path):
                    #         # Line numbers from search_function starts from 1
                    # start_line = key_condition.start_line - func_res.start_line - 1
                    # end_line = key_condition.end_line - func_res.start_line - 1
                    #         func_lines.insert(
                    #             start_line + added_lines, "/*BEGIN_KEY_CONDITION*/"
                    #         )
                    #         added_lines += 1
                    #         func_lines.insert(
                    #             end_line + 1 + added_lines, "/*END_KEY_CONDITION*/"
                    #         )
                    #         added_lines += 1
                    break

            func_body = f"// {func_res.func_name}\n"
            func_body += "\n".join(func_lines)
            func_str = make_func_prompt(
                func_res.func_name, func_body, func_res.file_path
            )
            function_bodies.append(func_str)

    return function_bodies


async def format_call_flow(
    gc: GlobalContext, cg: CG, nodes_in_path: list[FuncInfo]
) -> str:
    """Format control flow information showing paths to buggy functions."""
    if not nodes_in_path:
        return ""

    # if not cg.root_node:
    #     logger.warning("Call graph has no root node")
    #     return ""

    flow_lines = ["<CALL_FLOW>"]

    async def format_node(node: FuncInfo, depth: int) -> None:
        if node not in nodes_in_path:
            return

        indent = "  " * depth
        # Get full function name
        func_res = await search_function_with_path(
            gc.code_indexer, node.func_location.func_name, node.func_location.file_path
        )
        if func_res:
            # TODO: Check #262
            full_name = func_res.func_name
            flow_lines.append(f"{indent}↳ {full_name}")
        else:
            # flow_lines.append(f"{indent}↳ {node.func_location.func_name} [?]")
            pass

        for child in node.children:
            if child in nodes_in_path:
                await format_node(child, depth + 1)

    await format_node(cg.root_node, 0)

    flow_lines.append("</CALL_FLOW>")
    return "\n".join(flow_lines)


CODE_PROMPT = """This is the target code from the root function.
Each function is preceded by a comment showing its full qualified name.
If there is vulnerability, you may need to focus on the /*BUG_HERE*/ marker:
"""


async def format_source_codes(
    gc: GlobalContext,
    harness_path: Path,
    cg: CG,
    BITs: List[BugInducingThing] = [],
    nodes_to_include: Optional[List[FuncInfo]] = None,
) -> str:
    """Format source codes for a CG."""
    # Use provided nodes or compute them if not provided
    if nodes_to_include is None:
        nodes_to_include = []
        for bit in BITs:
            nodes_to_include.extend(find_nodes_in_path(cg, bit))

    if nodes_to_include:
        function_bodies = await collect_function_bodies_with_bits(
            gc, cg, BITs, nodes_to_include
        )
    else:
        # If BIT is not given, just use all CG functions
        function_bodies = await collect_all_function_bodies(gc, cg)

    source_code = "\n\n".join(function_bodies)
    source_code = f"{SOURCE_TAG}\n{source_code}\n{END_SOURCE_TAG}\n"

    # Format the prompt
    final_prompt = [CODE_PROMPT]

    # Add control flow showing paths to buggy functions
    call_flow = await format_call_flow(gc, cg, nodes_to_include)
    logger.info(f"Call flow for {cg.name}:\n{call_flow}")
    if call_flow:
        final_prompt.append(call_flow)

    # # Read harness code
    # # This is not necessary anymore as harness_path is given from CG. (#248)
    # harness_str = make_harness_prompt(harness_path)
    # if harness_str:
    #     final_prompt.append(harness_str)

    if source_code:
        final_prompt.append(source_code)

    return "\n\n".join(final_prompt)


def get_bit_code(proj_path: Path, bit: BugInducingThing) -> str:
    """Get code context for a BIT with surrounding lines."""
    if not bit.func_location.file_path:
        logger.error(f"BIT {bit.func_location.func_name} has no file path")
        return ""

    try:
        bit_fullpath = proj_path / bit.func_location.file_path
        with open(bit_fullpath, "r") as f:
            lines = f.readlines()

        # Get context (20 lines before and after)
        start_idx = max(0, bit.func_location.start_line - 20)
        end_idx = min(len(lines), bit.func_location.end_line + 20)
        bit_code = "".join(lines[start_idx:end_idx])

        return f"""Please focus on the below code when deciding applicable sanitizers:
        {CODE_TAG}
        {bit_code}
        {END_CODE_TAG}
        """
    except Exception as e:
        logger.error(f"Error reading BIT code from {bit_fullpath}: {e}")
        return ""


def init_single_cg_analysis_prompts(
    gc: GlobalContext,
    harness: sCP_Harness,
    cg: CG,
    BITs: List[BugInducingThing],
) -> str:
    """Analyze a single CG using BITs from cp."""

    async def _analyze():
        # Get BITs for this harness
        harness_bits = [bit for bit in BITs if bit.harness_name == harness.name]

        # Let format_source_codes handle the relevance check internally
        return await format_source_codes(gc, harness.src_path, cg, harness_bits)

    # Run async function in event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # If no event loop exists, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_analyze())


def init_cg_analysis_prompts(
    gc: GlobalContext,
    CGs: Dict[str, List[CG]],
    BITs: List[BugInducingThing],
) -> Dict[str, Dict[str, CodeInfo]]:
    """Initialize code analysis prompts for all harnesses."""

    async def _init_prompts():
        analyses = {}

        # Initialize harness to BITs mapping
        harness_to_BITs: Dict[str, List[BugInducingThing]] = {
            harness.name: [] for harness in gc.cp.harnesses.values()
        }
        for bit in BITs:
            if bit.harness_name in harness_to_BITs:
                harness_to_BITs[bit.harness_name].append(bit)

        # Generate analysis for each harness
        for harness in gc.cp.harnesses.values():
            if harness.name not in CGs:
                continue

            # Generate analysis for each CG
            cg_analyses = {}
            for cg in CGs[harness.name]:
                # Find matching BIT and nodes for this CG
                matching_bit = None
                matching_nodes = []

                # Only compute nodes once per CG-BIT pair
                for bit in harness_to_BITs[harness.name]:
                    nodes = find_nodes_in_path(cg, bit)
                    if nodes:  # If nodes found, this BIT matches this CG
                        matching_bit = bit
                        matching_nodes = nodes
                        break

                # Pass the pre-computed nodes to avoid recomputing
                cg_code = await format_source_codes(
                    gc,
                    harness.src_path,
                    cg,
                    [matching_bit] if matching_bit else [],
                    matching_nodes,
                )

                cg_analyses[cg.name] = CodeInfo(
                    cg=cg,
                    cg_code=cg_code,
                    bit=matching_bit,
                )
            analyses[harness.name] = cg_analyses

            logger.info(
                f"Generated analysis for harness {harness.name} "
                f"with {len(cg_analyses)} CGs"
            )

        return analyses

    # Run async function in event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # If no event loop exists, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_init_prompts())
