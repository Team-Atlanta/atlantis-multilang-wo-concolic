import functools
import json
import os
from typing import Dict, List, Optional

import clang.cindex


@functools.lru_cache(maxsize=1)
def is_running_under_pytest():
    return "PYTEST_CURRENT_TEST" in os.environ

@functools.lru_cache(maxsize=4)
def _walk_directory(root_dir: str) -> Dict[str, List[str]]:
    filename_to_path_dict : Dict[str, List[str]]= {}

    for root, _, files in os.walk(root_dir):
        for filename in files:
            if filename not in filename_to_path_dict:
                filename_to_path_dict[filename] = []
            filename_to_path_dict[filename].append(os.path.join(root, filename))
    
    return filename_to_path_dict


def get_new_file_path(old_file_path: str, new_project_root: str) -> Optional[str]:
    filename = os.path.basename(old_file_path)
    candidate_paths = _walk_directory(new_project_root).get(filename, [])

    if not candidate_paths:
        return None

    if len(candidate_paths) == 1:
        return candidate_paths[0]

    old_parts = old_file_path.split(os.sep)
    old_parts.reverse()
    best_match = None
    longest_match_length = 0
    longest_match_candidate_parts_count = 0

    for candidate_path in candidate_paths:
        candidate_parts = candidate_path.split(os.sep)
        candidate_parts.reverse()

        match_length = 0
        while (
            match_length < len(old_parts)
            and match_length < len(candidate_parts)
            and old_parts[match_length] == candidate_parts[match_length]
        ):
            match_length += 1

        if match_length > longest_match_length or (
            match_length == longest_match_length
            and len(candidate_parts) < longest_match_candidate_parts_count
        ):
            longest_match_length = match_length
            best_match = candidate_path
            longest_match_candidate_parts_count = len(candidate_parts)

    return best_match


def common_path_suffix(path1, path2):
    parts1 = path1.strip(os.sep).split(os.sep)
    parts2 = path2.strip(os.sep).split(os.sep)

    common_parts = []

    for p1, p2 in zip(reversed(parts1), reversed(parts2)):
        if p1 == p2:
            common_parts.insert(0, p1)
        else:
            break

    if not common_parts:
        return ""

    return os.sep.join(common_parts)


def extract_clang_args(
    src_file: str, compile_commands_path: str = "/src/repo/compile_commands.json"
) -> List[str]:
    try:
        if not os.path.isfile(compile_commands_path):
            return []

        with open(compile_commands_path, "r") as f:
            compile_commands = json.load(f)

        src_file_abs = os.path.abspath(src_file)
        best_match = None
        longest_suffix = ""

        for entry in compile_commands:
            try:
                entry_dir = entry.get("directory", "")
                entry_file = entry.get("file", "")
                full_entry_path = os.path.abspath(os.path.join(entry_dir, entry_file))

                if os.path.basename(src_file_abs) != os.path.basename(full_entry_path):
                    continue

                suffix = common_path_suffix(src_file_abs, full_entry_path)
                if len(suffix) > len(longest_suffix):
                    best_match = (entry, entry_dir, full_entry_path, suffix)
                    longest_suffix = suffix

            except Exception:
                continue

        if best_match is None:
            return []

        entry, entry_dir, full_entry_path, common_suffix = best_match

        real_src_prefix = src_file_abs[: -len(common_suffix)]
        entry_src_prefix = full_entry_path[: -len(common_suffix)]

        args = entry.get("arguments", [])
        clang_args = []

        for arg in args:
            if arg.startswith("-I"):
                include_path = arg[2:]

                if not os.path.isabs(include_path):
                    abs_entry_path = os.path.abspath(
                        os.path.join(entry_dir, include_path)
                    )
                else:
                    abs_entry_path = include_path

                if abs_entry_path.startswith(entry_src_prefix):
                    adjusted_path = abs_entry_path.replace(
                        entry_src_prefix, real_src_prefix, 1
                    )
                else:
                    adjusted_path = abs_entry_path

                clang_args.append(f"-I{adjusted_path}")

        return clang_args

    except Exception:
        pass

    return []


def map_lines_to_functions(src_file: str) -> Dict[int, str]:
    args = extract_clang_args(src_file)
    index = clang.cindex.Index.create()
    tu = index.parse(src_file, args=args)

    function_map: Dict[int, str] = {}

    def visit(node, parent_name=None):
        func_name = None
        if not node.extent.start.file or node.extent.start.file.name != src_file:
            return

        if node.kind == clang.cindex.CursorKind.FUNCTION_DECL:
            func_name = node.spelling
        elif node.kind == clang.cindex.CursorKind.FUNCTION_TEMPLATE:
            func_name = node.spelling
        elif node.kind == clang.cindex.CursorKind.CXX_METHOD:
            func_name = f"{node.semantic_parent.spelling}::{node.spelling}"
        elif node.kind == clang.cindex.CursorKind.CONSTRUCTOR:
            func_name = f"{node.semantic_parent.spelling}::{node.spelling}"
        elif node.kind == clang.cindex.CursorKind.DESTRUCTOR:
            func_name = f"{node.semantic_parent.spelling}::~{node.spelling}"

        if func_name:
            start_line = node.extent.start.line
            end_line = node.extent.end.line
            for line in range(start_line, end_line + 1):
                function_map[line] = func_name

        for child in node.get_children():
            visit(child, func_name)

    visit(tu.cursor)
    return function_map
