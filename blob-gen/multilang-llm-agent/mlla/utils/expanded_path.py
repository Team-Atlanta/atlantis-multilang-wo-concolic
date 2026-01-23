from typing import Dict, List, Optional

from loguru import logger

from mlla.utils import instrument_line
from mlla.utils.cg import FuncInfo


class ExpandedPath:
    def __init__(self, path_list: List[List[FuncInfo]]):
        self.path_list = path_list
        self.instrumented_code: Dict[str, str] = {}
        self._instrument_code()

    def _instrument_code(self):
        """Combine code snippets from path into a single string"""
        flattened_list = [node for group in self.path_list for node in group]
        for node in flattened_list:
            if not node.func_body:
                continue
            func_body, _ = instrument_line(
                node.func_body, node.func_location.start_line
            )
            self.instrumented_code[node.create_tag()] = (
                f"<func_name>{node.func_location.func_name}</func_name>\n"
                f"<file_path>{node.func_location.file_path}</file_path>\n"
                f"<func_prototype_and_func_body>\n{func_body}\n"
                "</func_prototype_and_func_body>"
            )

    def get_call_flow(self, path_list: Optional[List[List[FuncInfo]]] = None) -> str:
        call_flow_lines = []
        indentation_char = "  "  # Two spaces for indentation
        if path_list is None:
            path_list = self.path_list

        for i, group in enumerate(path_list):
            indent = indentation_char * i
            for j, node in enumerate(group):
                if not node.func_location or not node.func_location.func_name:
                    continue  # Should not happen based on filtering, but safe check
                if j == 0:
                    call_flow_lines.append(f"{indent}↳ {node.func_location.func_name}")
                else:
                    call_flow_lines.append(
                        f"{indent}{indentation_char}↳ {node.func_location.func_name}"
                    )
        return "\n".join(call_flow_lines)

    def indent_line(self, lines: str, indent: int) -> str:
        space = " " * indent
        return "\n".join([f"{space}{line}" for line in lines.splitlines()])

    def code_with_path(
        self,
        _path_list: Optional[List[List[FuncInfo]]] = None,
        include_diff: bool = True,
    ) -> str:
        if _path_list is None:
            path_list = [node for group in self.path_list for node in group]
        else:
            path_list = [node for group in _path_list for node in group]

        snippets = []
        for node in path_list:
            snippet = ""
            if node.create_tag() in self.instrumented_code:
                snippet += "<function>\n"
                code = self.instrumented_code[node.create_tag()]
                snippet += self.indent_line(code, 2)

                if (
                    include_diff
                    and node.interest_info
                    and node.interest_info.is_interesting
                    and node.interest_info.diff
                ):
                    snippet += "\n"
                    diff = f"<diff>\n{node.interest_info.diff}\n</diff>"
                    snippet += self.indent_line(diff, 2)
                snippet += "\n</function>"
                snippets.append(snippet)
            else:
                logger.warning(
                    f"No instrumented code for {node.func_location.func_name}"
                )

        code_snippet = "\n\n".join(snippets)
        return code_snippet

    def contain_interesting_node(self) -> bool:
        for group in self.path_list:
            for node in group:
                if node.interest_info and node.interest_info.is_interesting:
                    return True
        return False

    def __str__(self):
        return f"Call flow:\n{self.get_call_flow()}\n\nCode:\n{self.code_with_path()}"
