import re
from pathlib import Path
from typing import Any

from ast_grep_py import SgNode, SgRoot
from langchain.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel
from tokencost import count_string_tokens

from ...codeindexer.codeindexer import CIFunctionRes
from ...utils import instrument_line
from ..llm import PrioritizedTool


class RetrievalResult(BaseModel):
    code: str = ""
    lang: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    column_start: int = 0
    column_end: int = 0
    name: str = ""

    def to_cifunctionres(self) -> CIFunctionRes:
        return CIFunctionRes(
            func_name=self.name,
            file_path=self.file_path,
            func_body=self.code,
            start_line=self.line_start,
            end_line=self.line_end,
        )

    def pretty_str(self) -> str:
        instrumented_code, _ = instrument_line(self.code, self.line_start)
        return (
            f"File path: {self.file_path}\n"
            f"Language: {self.lang}\n"
            f"Start line: {self.line_start}\n"
            f"End line: {self.line_end}\n"
            f"Name: {self.name}\n"
            f"Code: ```\n{instrumented_code}\n```\n"
        )


class AGTool:
    def __init__(self):
        self.type_only_param = False

    def _get_root_node(self, file_path: str, lang: str) -> tuple[SgNode, str]:
        with open(file_path, "r", encoding="utf-8") as f:
            file_src = f.read()
        return SgRoot(file_src, lang).root(), file_src

    def _retrieve_code_from_node(
        self, node: SgNode, file_src: str, matched_name: str
    ) -> dict[str, Any]:
        # https://ast-grep.github.io/guide/rule-config/atomic-rule.html#range
        # >> line and column are 0-based and character-wise,
        # >> and the start is inclusive while the end is exclusive.
        # DO NOT think of `line` and `column` are separate. It's a `Pos` together.
        # That's the reason for the misperception that end.line seems inclusive.
        start = node.range().start
        end = node.range().end

        # Instead of sg_node.text(), we extract the code using the line numbers.
        # This is due to string formatting issues in the text() method
        # which does not return the exact code in the source file.
        code_lines = file_src.splitlines()[start.line : end.line + 1]
        # Process the end line first to avoid a bug when there is only one line.
        code_lines[-1] = code_lines[-1][: end.column]
        code_lines[0] = code_lines[0][start.column :]
        # Convert to 1-based and inclusive end for editor-like experience.
        return {
            "code": "\n".join(code_lines),
            "line_start": start.line + 1,
            "line_end": end.line + 1,
            "column_start": start.column + 1,
            "column_end": end.column,
            "name": matched_name,
        }

    def _language_from_file_path(self, file_path: str) -> str:
        file_extention = Path(file_path).suffix.lower()
        lang = ""
        if file_extention in (".c", ".h"):
            lang = "c"
        elif file_extention in (".cpp", ".cc", ".hpp"):
            lang = "cpp"
        elif file_extention == ".java":
            lang = "java"
        elif file_extention == ".py":
            lang = "python"
        else:
            logger.error(f"Unsupported file extension: {file_path}")
            lang = "c"
        return lang

    def search_function_definition(
        self,
        regex: str,
        file_path: str,
    ) -> list[RetrievalResult]:
        """
        Search for a function definition in a file.
        :param regex: The regular expression to search for.
        :param file_path: The path to the file.
        :return: A list of RetrievalResult objects.
        """
        lang = self._language_from_file_path(file_path)
        if lang == "java":
            return self._search_function_definition_Java(regex, file_path)
        else:
            return self._search_function_definition_C(regex, file_path)

    def search_type_definition(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        """
        Search for a type definition in a file.
        :param regex: The regular expression to search for.
        :param file_path: The path to the file.
        :return: A list of RetrievalResult objects.
        """
        lang = self._language_from_file_path(file_path)
        if lang == "java":
            return self._search_type_definition_Java(regex, file_path)
        else:
            return self._search_type_definition_C(regex, file_path)

    def count_anon_siblings(self, node: SgNode) -> int:
        parent = node
        while parent is not None:
            if parent.kind() in ["class_body", "interface_body", "enum_body"]:
                break
            parent = parent.parent
        if parent is None:
            return 0

        anon_classes: list[SgNode] = []

        def traverse(node):
            if node.kind() == "object_creation_expression":
                if any(child.kind() == "class_body" for child in node.children()):
                    anon_classes.append(node)

            for child in node.children():
                traverse(child)

        traverse(parent)

        anon_counter = 0
        for anon_class in anon_classes:
            if anon_class.range().start.line <= node.range().start.line:
                anon_counter += 1
        return anon_counter

    def get_qualified_class_name(self, node: SgNode, package_name: str) -> str:
        """
        Follow the parent of the node to find all class_declarations
        and enum_declarations,
        collecting their names in order from outer to inner.
        Example: packageName.Outer$Inner$LocalClass, etc.
        """

        class_names = []
        current = node.parent()
        while current is not None:
            if current.kind() in [
                "class_declaration",
                "enum_declaration",
                "interface_declaration",
            ]:
                name_node = current.field("name")
                if name_node:
                    class_names.append(name_node.text())
            # Note: We do not care about anonymous classes for now
            elif current.kind() == "object_creation_expression":
                if any(child.kind() == "class_body" for child in current.children()):
                    anon_counter = self.count_anon_siblings(current)
                    class_names.append(f"{anon_counter}")

            current = current.parent()

        class_names.reverse()
        qualified = "$".join(class_names)
        if package_name:
            qualified = package_name + "." + qualified
        return qualified

    def get_parameter_types(self, params_node: SgNode) -> str:
        param_types: list[str] = []
        if params_node:
            for child in params_node.children():
                if child.kind() == "formal_parameter":
                    type_node = child.field("type")
                    if type_node:
                        param_types.append(type_node.text())
        return f"({', '.join(param_types)})"

    def get_preproc_contexts(self, node: SgNode) -> str:
        preproc_types: list[str] = [
            "preproc_ifdef",
            "preproc_ifndef",
            "preproc_if",
            "preproc_elif",
            "preproc_else",
            "preproc_endif",
        ]

        preproc_if_types: list[str] = [
            "preproc_ifdef",
            "preproc_ifndef",
            "preproc_if",
            "preproc_elif",
        ]

        preproc_contexts: list[str] = []
        current = node
        while current is not None and current.parent() is not None:
            parent = current.parent()
            if parent.kind() in preproc_types:
                context_str = parent.kind().replace("preproc_", "#")

                if parent.kind() in preproc_if_types:
                    for child in parent.children():
                        if child.kind() == "identifier":
                            condition = child.text()
                            context_str += f" {condition}"
                            break

                preproc_contexts.append(context_str)
            current = parent

        preproc_contexts.reverse()

        if preproc_contexts:
            return " -> ".join(preproc_contexts)
        return ""

    def _search_function_definition_Java(
        self, fn_name: str, file_path: str
    ) -> list[RetrievalResult]:
        import regex

        retrieval_results: list[RetrievalResult] = []
        lang = self._language_from_file_path(file_path)
        root_node, file_src = self._get_root_node(file_path, lang)

        package_node = root_node.find(kind="package_declaration")
        package_name = package_node.text() if package_node else ""

        # Find function_definition nodes
        method_declaration_nodes = root_node.find_all(
            regex=fn_name,
            kind="method_declaration",
            has={
                "kind": "identifier",
                "regex": fn_name,
                "field": "name",
                "stopBy": "neighbor",
            },
        )

        if len(method_declaration_nodes) > 0:
            for node in method_declaration_nodes:
                id_node = node.find(kind="identifier", regex=fn_name)
                type_node = node.field("type")
                parameters_node = node.field("parameters")
                method_name = id_node.text() if id_node else ""
                return_type = type_node.text() if type_node else ""

                qualified_class_name = self.get_qualified_class_name(node, package_name)

                if self.type_only_param:
                    params = self.get_parameter_types(parameters_node)
                else:
                    params = parameters_node.text() if parameters_node else ""

                matched_signature = (
                    f"{return_type} {qualified_class_name}.{method_name}{params}"
                )

                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        **self._retrieve_code_from_node(
                            node, file_src, matched_signature
                        ),
                    )
                )
            return retrieval_results

        constructor_declaration_nodes = root_node.find_all(
            regex=fn_name,
            kind="constructor_declaration",
            has={
                "kind": "identifier",
                "regex": fn_name,
                "field": "name",
                "stopBy": "neighbor",
            },
        )

        if len(constructor_declaration_nodes) > 0:
            for node in constructor_declaration_nodes:
                id_node = node.find(kind="identifier", regex=fn_name)
                parameters_node = node.field("parameters")
                constructor_name = id_node.text() if id_node else ""
                qualified_class_name = self.get_qualified_class_name(node, package_name)
                if self.type_only_param:
                    params = self.get_parameter_types(parameters_node)
                else:
                    params = parameters_node.text() if parameters_node else ""

                matched_signature = f"{qualified_class_name}.{constructor_name}{params}"

                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        **self._retrieve_code_from_node(
                            node, file_src, matched_signature
                        ),
                    )
                )
            return retrieval_results

        # Fallback to naive regex search for partial matches
        if len(retrieval_results) == 0 and not fn_name.startswith(r"\b"):

            # Build a pattern that captures the entire method definition.
            # We define a named group "body" that matches the method body
            # (including nested braces) recursively.
            pattern = (
                r"(?ms)(?P<method>"  # Capture the entire method definition as
                # group 'method'
                r"^\s*(?:public|protected|private)?\s*"  # Optional access
                # modifier with leading whitespace
                r"(?:static\s+)?(?:final\s+)?[\w\[\]<>]+\s+"  # Optional
                # 'static'/'final' and return type
                + fn_name  # The target method name
                + r"\s*\([^)]*\)\s*"  # Parameter list in parentheses (non-nested)
                r"(?:throws\s+[\w\s,]+)?\s*"  # Optional throws clause
                r"(?P<body>\{(?:(?>[^{}]+)|(?&body))*\})"  # Named group 'body':
                # matches braces recursively
                r")"
            )

            compiled_pattern = regex.compile(pattern, regex.VERBOSE)

            matches = compiled_pattern.findall(file_src)
            if matches:
                # Since we used a named group "method", each match is the full
                # method definition.
                for m in matches:
                    code = m[0]
                    line_start = file_src.split(code)[0].count("\n") + 1
                    line_end = line_start + 2 + code.count("\n")
                    retrieval_results.append(
                        RetrievalResult(
                            lang=lang,
                            file_path=file_path,
                            code=code,
                            line_start=line_start,
                            line_end=line_end,
                        )
                    )

        return retrieval_results

    def _search_function_definition_C(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        retrieval_results: list[RetrievalResult] = []
        lang = self._language_from_file_path(file_path)
        root_node, file_src = self._get_root_node(file_path, lang)

        def _make_signature(node: SgNode) -> str:
            id_node = node.find(kind="identifier", regex=regex)
            func_name = id_node.text() if id_node else ""
            return_type = node.field("type").text() if node.field("type") else ""
            params = node.field("parameters").text() if node.field("parameters") else ""
            context = node.field("scope").text() if node.field("scope") else ""
            signature = f"{return_type} {context}::{func_name}{params}"

            preproc_context = self.get_preproc_contexts(node)

            if preproc_context:
                signature = f"{signature} [{preproc_context}]"
            return signature

        # Find function_definition nodes
        function_declarator_nodes = root_node.find_all(
            regex=regex,
            kind="function_definition",
            has={
                "kind": "function_declarator",
                "regex": regex,
                "field": "declarator",
                "stopBy": "end",
                "has": {
                    "kind": "identifier",
                    "regex": regex,
                    "field": "declarator",
                    "stopBy": "neighbor",
                },
            },
        )

        for node in function_declarator_nodes:
            signature = _make_signature(node)

            retrieval_results.append(
                RetrievalResult(
                    lang=lang,
                    file_path=file_path,
                    **self._retrieve_code_from_node(node, file_src, signature),
                )
            )
        if len(retrieval_results) > 0:
            return retrieval_results

        # Fallback to preproc_function_def if function_definition not found
        preproc_function_def_nodes = root_node.find_all(
            regex=regex,
            kind="preproc_function_def",
            has={"kind": "identifier", "regex": regex, "stopBy": "neighbor"},
        )

        for node in preproc_function_def_nodes:
            signature = _make_signature(node)
            retrieval_results.append(
                RetrievalResult(
                    lang=lang,
                    file_path=file_path,
                    **self._retrieve_code_from_node(node, file_src, signature),
                )
            )
        if len(retrieval_results) > 0:
            return retrieval_results

        # Fallback to naive regex search for partial matches
        simple_regex = rf"\b{regex}\s*\([^)]*\)\s*\{{"
        potential_matches = list(re.finditer(simple_regex, file_src))

        for match in potential_matches:
            start_pos = match.start()
            brace_count = 1
            end_pos = match.end()

            for i in range(match.end(), len(file_src)):
                if file_src[i] == "{":
                    brace_count += 1
                elif file_src[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        break

            if brace_count == 0:
                code = file_src[start_pos:end_pos]
                line_start = file_src[:start_pos].count("\n") + 1
                line_end = line_start + code.count("\n")
                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        code=code,
                        line_start=line_start,
                        line_end=line_end,
                    )
                )

        return retrieval_results

    def _search_type_definition_Java(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        """
        Search for a type definition in a file.
        :param regex: The regular expression to search for.
        :param file_path: The path to the file.
        :return: A list of RetrievalResult objects.
        """
        retrieval_results: list[RetrievalResult] = []
        lang = self._language_from_file_path(file_path)
        root_node, file_src = self._get_root_node(file_path, lang)

        type_definition_nodes = root_node.find_all(
            regex=regex,
            kind="class_declaration",
            has={
                "kind": "identifier",
                "regex": regex,
                "field": "name",
                "stopBy": "end",
            },
        )

        type_definition_nodes += root_node.find_all(
            regex=regex,
            kind="interface_declaration",
            has={
                "kind": "identifier",
                "regex": regex,
                "field": "name",
                "stopBy": "end",
            },
        )

        type_definition_nodes += root_node.find_all(
            regex=regex,
            kind="enum_declaration",
            has={
                "kind": "identifier",
                "regex": regex,
                "field": "name",
                "stopBy": "end",
            },
        )

        type_definition_nodes += root_node.find_all(
            regex=regex,
            kind="annotation_type_declaration",
            has={
                "kind": "identifier",
                "regex": regex,
                "field": "name",
                "stopBy": "end",
            },
        )
        for node in type_definition_nodes:
            id_node = node.find(kind="identifier", regex=regex)
            matched_name = id_node.text() if id_node else ""
            retrieval_results.append(
                RetrievalResult(
                    lang=lang,
                    file_path=file_path,
                    **self._retrieve_code_from_node(node, file_src, matched_name),
                )
            )

        return retrieval_results

    def _search_type_definition_C(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        """
        Search for a type definition in a file.
        :param regex: The regular expression to search for.
        :param file_path: The path to the file.
        :return: A list of RetrievalResult objects.
        """
        retrieval_results: list[RetrievalResult] = []
        lang = self._language_from_file_path(file_path)
        root_node, file_src = self._get_root_node(file_path, lang)

        type_definition_nodes = root_node.find_all(
            regex=regex,
            kind="type_definition",
            has={
                "kind": "type_identifier",
                "regex": regex,
                "field": "declarator",
                "stopBy": "end",
            },
        )

        for node in type_definition_nodes:
            type_identifier_node = node.find(kind="type_identifier", regex=regex)
            if type_identifier_node is None:
                continue
            elif type_identifier_node.inside(kind="parameter_list"):
                continue
            matched_name = type_identifier_node.text()
            retrieval_results.append(
                RetrievalResult(
                    lang=lang,
                    file_path=file_path,
                    **self._retrieve_code_from_node(node, file_src, matched_name),
                )
            )
            if node.has(kind="field_declaration_list"):
                continue

            # Provide additional struct for type_definition without
            # field_declaration_list
            struct_specifier_node = node.find(kind="struct_specifier")
            if struct_specifier_node is not None:
                struct_name_node = struct_specifier_node.find(kind="type_identifier")
                if struct_name_node is not None:
                    struct_name_regex = rf"\b{struct_name_node.text()}\b"
                    struct_nodes = root_node.find_all(
                        regex=struct_name_regex,
                        kind="struct_specifier",
                        has={"kind": "field_declaration_list", "stopBy": "neighbor"},
                    )
                    for struct_node in struct_nodes:
                        retrieval_results.append(
                            RetrievalResult(
                                lang=lang,
                                file_path=file_path,
                                **self._retrieve_code_from_node(
                                    struct_node, file_src, matched_name
                                ),
                            )
                        )

        # Fallback to struct_specifier if type_definition not found
        if len(retrieval_results) == 0:
            struct_specifier_nodes = root_node.find_all(
                regex=regex,
                kind="struct_specifier",
                has={"kind": "type_identifier", "regex": regex, "stopBy": "neighbor"},
            )
            for node in struct_specifier_nodes:
                id_node = node.find(kind="type_identifier", regex=regex)
                matched_name = id_node.text() if id_node else ""
                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        **self._retrieve_code_from_node(node, file_src, matched_name),
                    )
                )

        # Fallback to preproc_def
        if len(retrieval_results) == 0:
            preproc_def_nodes = root_node.find_all(
                regex=regex,
                kind="preproc_def",
                has={"kind": "identifier", "regex": regex, "stopBy": "neighbor"},
            )
            for node in preproc_def_nodes:
                id_node = node.find(kind="identifier", regex=regex)
                matched_name = id_node.text() if id_node else ""
                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        **self._retrieve_code_from_node(node, file_src, matched_name),
                    )
                )

        return retrieval_results


class AGSchema(BaseModel):
    regex: str
    file_path: str


def create_ag_tools() -> list[PrioritizedTool]:

    ag_tool = AGTool()

    def search_function_definition_tool_function(
        regex: str, file_path: str
    ) -> list[str]:
        if not regex or not file_path:
            raise Exception("You must provide a regex pattern and a file path.")
        if Path(file_path).is_dir():
            raise Exception("The file_path cannot be a directory.")
        results = ag_tool.search_function_definition(regex, file_path)
        results_str = str(results)
        token_cnt = count_string_tokens(results_str, "gpt-4o")
        if token_cnt > 120000:
            raise Exception(
                "The results are too long. Please provide a more specific query."
            )
        return [r.pretty_str() for r in results]

    def search_type_definition_tool_function(regex: str, file_path: str) -> list[str]:
        if not regex or not file_path:
            raise Exception("You must provide a regex pattern and a file path.")
        if Path(file_path).is_dir():
            raise Exception("The file_path cannot be a directory.")
        results = ag_tool.search_type_definition(regex, file_path)
        results_str = str(results)
        token_cnt = count_string_tokens(results_str, "gpt-4o")
        if token_cnt > 120000:
            raise Exception(
                "The results are too long. Please provide a more specific query."
            )
        return [r.pretty_str() for r in results]

    tools = [
        StructuredTool.from_function(
            name="search_function_definition",
            func=search_function_definition_tool_function,
            args_schema=AGSchema,
            description=(
                "Search tool that retrieves function definitions from a given file"
                "_path. The regex pattern is used to match the function name."
                " The file_path cannot be a directory."
            ),
        ),
        StructuredTool.from_function(
            name="search_type_definition",
            func=search_type_definition_tool_function,
            args_schema=AGSchema,
            description=(
                "Search tool that retrieves type definitions from a given file_path."
                " The regex pattern is used to match the type name."
                " The file_path cannot be a directory."
            ),
        ),
    ]

    return PrioritizedTool.from_tools(tools, priority=2)
