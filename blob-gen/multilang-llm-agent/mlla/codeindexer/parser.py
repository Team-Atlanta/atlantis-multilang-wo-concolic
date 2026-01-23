import dataclasses

# import subprocess
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger
from tree_sitter import Node, Parser

from ..codeindexer.tree_sitter_languages import get_language
from ..utils import instrument_line


@dataclasses.dataclass
class CIFunctionRes:
    func_name: str
    file_path: str
    start_line: int
    end_line: int
    func_body: str

    def pretty_str(self) -> str:
        instrumented_fn_body, _ = instrument_line(self.func_body, self.start_line)
        return (
            f"Function name: {self.func_name}\n"
            f"File path: {self.file_path}\n"
            f"Start line: {self.start_line}\n"
            f"End line: {self.end_line}\n"
            f"Function body: ```\n{instrumented_fn_body}\n```\n"
        )


@dataclasses.dataclass
class InternalFunctionRes(CIFunctionRes):
    type_only_func_name: Optional[str] = None


class BaseParser(ABC):
    language: str
    tree_sitter_query: str

    @abstractmethod
    async def parse_file(
        self, file_path: Path
    ) -> Tuple[Dict[str, InternalFunctionRes], Dict[str, List[InternalFunctionRes]]]:
        raise NotImplementedError


class CppParser(BaseParser):
    empty_identifier = "__EMPTY_NAMESPACE_NAME__"

    def __init__(self, type_only_param=False) -> None:
        self.language = "cpp"
        self.tree_sitter_query = """
(function_definition
    type: (_)? @return_type
    declarator: (_)
) @function_body

(preproc_function_def name: (identifier) @function_name) @function_body

(preproc_def name: (identifier) @function_name) @function_body

(struct_specifier name: (type_identifier) @function_name) @function_body

(union_specifier name: (type_identifier) @function_name) @function_body

(type_definition declarator: (_) @identifier_wrapper) @function_body

(enum_specifier name: (type_identifier) @function_name) @function_body
"""
        self.func_ptr_query = """
(function_declarator
    declarator: (parenthesized_declarator
        (pointer_declarator declarator: (function_declarator) @function_declarator))
    parameters: (_) @fp_params
)
"""
        self.type_only_param = type_only_param

    def get_context(self, node) -> str:
        # Exclude itself
        current = node.parent

        class_names = []
        while current is not None:
            if current.type in [
                "class_specifier",
                "namespace_definition",
                "struct_specifier",
            ]:
                name_node = current.child_by_field_name("name")
                if name_node:
                    class_names.append(name_node.text.decode())
                elif current.type == "namespace_definition":
                    # Anonymous namespace
                    class_names.append(self.empty_identifier)
            current = current.parent

        class_names.reverse()
        context = "::".join(class_names)
        return context

    def extract_function_name(
        self, function_name_wrapper: Node
    ) -> Tuple[Optional[Node], Optional[Node], Optional[Node]]:
        emsgs = []

        current = function_name_wrapper
        if current.type == "function_declarator":
            declarator = current.child_by_field_name("declarator")
            params = current.child_by_field_name("parameters")

            if declarator and declarator.type == "identifier":
                return None, declarator, params
            elif declarator and declarator.type == "qualified_identifier":
                return (
                    declarator.child_by_field_name("scope"),
                    declarator.child_by_field_name("name"),
                    params,
                )
            elif declarator and declarator.type == "field_identifier":
                return None, declarator, params
            elif declarator and declarator.type == "destructor_name":
                return None, declarator, params
            elif declarator and declarator.type == "constructor_name":
                return None, declarator, params
            else:
                emsgs.append(
                    f"Function has no name: {current}. Needed to check the query."
                )
                if current.text:
                    emsgs.append(f"Check this >\n{current.text.decode()}")
        else:
            emsgs.append(f"Function has no name: {current}. Needed to check the query.")
            if current.text:
                emsgs.append(f"Check this >\n{current.text.decode()}")

        for emsg in emsgs:
            logger.warning(emsg)
        return None, None, None

    def extract_type_identifier(self, identifier_wrapper: Node) -> Optional[Node]:
        node = [identifier_wrapper]

        while node:
            current = node.pop()
            if current.type == "type_identifier":
                return current
            node.extend(current.children)

        return None

    def get_parameter_types(self, params_node) -> str:
        def _parse_decorator(node) -> str:
            if node.type == "pointer_declarator":
                return "*" + _parse_decorator(node.child_by_field_name("declarator"))
            elif node.type == "reference_declarator":
                return "&" + _parse_decorator(node.children[0])
            elif node.type == "array_declarator":
                return _parse_decorator(node.child_by_field_name("declarator")) + "[]"
            else:
                return ""

        param_types = []
        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    type_node = child.child_by_field_name("type")
                    decorator = child.child_by_field_name("declarator")
                    if type_node:
                        if decorator:
                            param_types.append(
                                type_node.text.decode() + _parse_decorator(decorator)
                            )
                        else:
                            param_types.append(type_node.text.decode())
        return f"({', '.join(param_types)})"

    async def parse_file(
        self, file_path: Path
    ) -> Tuple[Dict[str, InternalFunctionRes], Dict[str, List[InternalFunctionRes]]]:
        tree_sitter_lang = get_language(self.language)
        parser = Parser(tree_sitter_lang)

        with open(file_path, "rb") as f:
            source_code = f.read()

        tree = parser.parse(source_code)
        query = tree_sitter_lang.query(self.tree_sitter_query)
        func_ptr_query = tree_sitter_lang.query(self.func_ptr_query)

        hash_mapped_data: Dict = {}
        set_mapped_data = defaultdict(list)
        matches = query.matches(tree.root_node)
        matches.sort(key=lambda x: x[0])

        for query_id, match in matches:
            try:
                return_type = ""
                type_only_params = ""
                if query_id == 0:
                    # Pointer unwrap
                    function_body = match["function_body"][0]
                    declarator = function_body.child_by_field_name("declarator")
                    ptr_count = 0
                    ref_count = 0
                    while declarator:
                        if declarator.type == "pointer_declarator":
                            ptr_count += 1
                            declarator = declarator.child_by_field_name("declarator")
                        elif declarator.type == "reference_declarator":
                            ref_count += 1
                            declarator = declarator.children[1]
                        elif declarator.type == "ERROR":
                            logger.warning(
                                "Tree-sitter parsing error:"
                                f" {function_body}\n{declarator.text.decode()}"
                            )
                            declarator = declarator.children[0]
                        else:
                            break

                    if "return_type" in match:
                        return_type = match["return_type"][0].text.decode()
                        if ptr_count > 0:
                            return_type = f"{return_type}{'*'*ptr_count}"
                        if ref_count > 0:
                            return_type = f"{return_type}{'&'*ref_count}"

                    func_ptr = func_ptr_query.captures(declarator)
                    if func_ptr:
                        declarator = func_ptr["function_declarator"][0]
                        fp_params = func_ptr["fp_params"][0].text.decode()
                        return_type = f"{return_type} (*){fp_params}"

                    context, _func_name, params = self.extract_function_name(declarator)
                    if _func_name is None:
                        continue
                    func_name = _func_name.text.decode()
                    type_only_params = (
                        self.get_parameter_types(params) if params else ""
                    )
                    params = params.text.decode() if params else ""
                elif "identifier_wrapper" in match:
                    context = None
                    _func_name = self.extract_type_identifier(
                        match["identifier_wrapper"][0]
                    )
                    if _func_name is None:
                        continue
                    func_name = _func_name.text.decode()
                    params = ""
                else:
                    context = None
                    func_name = match["function_name"][0].text.decode()
                    params = ""

                function = match["function_body"][0]
                if context:
                    # Not sure that this does not require additional context check
                    signature = f"{context.text.decode()}::{func_name}{params}"
                else:
                    context = self.get_context(function)
                    if context:
                        signature = f"{context}::{func_name}{params}"
                    else:
                        signature = f"{func_name}{params}"
                    signature = signature.replace(self.empty_identifier, "")

                if "return_type" in match:
                    # return_type = match["return_type"][0].text.decode()
                    signature = f"{return_type} {signature}"

                preproc_context = self.get_preproc_contexts(function)
                # Add preprocessor context if available
                if preproc_context:
                    signature = f"{signature} [{preproc_context}]"

                if query_id == 1 or query_id == 2:
                    end_line_padding = 0
                else:
                    end_line_padding = 1

                type_only_param_signature = (
                    signature.replace(params, type_only_params)
                    if type_only_params
                    else ""
                )

                data = InternalFunctionRes(
                    func_name=signature,
                    file_path=str(file_path),
                    start_line=function.start_point.row + 1,
                    end_line=function.end_point.row + end_line_padding,
                    func_body=function.text.decode(),
                    type_only_func_name=type_only_param_signature,
                )
                """
                Current version only use set_mapped_data to search
                multiple functions that have the same name
                """
                # hash_mapped_data[signature] = data
                set_mapped_data[func_name].append(data)
            except Exception as e:
                logger.error(f"Error processing {file_path}: {str(e)}")
                continue

        return hash_mapped_data, set_mapped_data

    def get_preproc_contexts(self, node):
        """
        Traverse up the parent chain to find all preprocessor directives
        that contain this node and build a context string.
        This handles nested preprocessor directives properly.
        """
        preproc_types = [
            "preproc_ifdef",
            "preproc_ifndef",
            "preproc_if",
            "preproc_elif",
            "preproc_else",
            "preproc_endif",
        ]

        contexts = []
        current = node

        # Go up the parent chain to collect all preprocessor contexts
        while current and current.parent:
            parent = current.parent

            # Check if parent is a preprocessor directive
            if parent.type in preproc_types:
                context_str = parent.type.replace("preproc_", "#")

                # For directives that have conditions, try to extract them
                if parent.type in [
                    "preproc_ifdef",
                    "preproc_ifndef",
                    "preproc_if",
                    "preproc_elif",
                ]:
                    # Look for condition in children
                    for child in parent.children:
                        if child.type == "identifier":
                            condition = child.text.decode().strip()
                            context_str += f" {condition}"
                            break

                contexts.append(context_str)
            current = parent

        # Reverse to get from outermost to innermost directive
        contexts.reverse()

        if contexts:
            return " -> ".join(contexts)
        return ""


class JavaParser(BaseParser):
    def __init__(self, type_only_param=False) -> None:
        self.language = "java"
        self.tree_sitter_query = """
(
  (method_declaration
    type: (_) @return_type
    name: (identifier) @method_name
    parameters: (formal_parameters) @params
  ) @method_declaration
)

(
  (constructor_declaration
    name: (identifier) @constructor_name
    parameters: (formal_parameters) @constructor_params
  ) @constructor_declaration
)
"""
        self.type_only_param = type_only_param

    def count_anon_siblings(self, node):
        parent = node
        while parent is not None:
            if parent.type in ["class_body", "interface_body", "enum_body"]:
                break
            parent = parent.parent
        if parent is None:
            return 0

        anon_classes = []

        def traverse(node):
            if node.type == "object_creation_expression":
                if any(child.type == "class_body" for child in node.children):
                    anon_classes.append(node)

            for child in node.children:
                traverse(child)

        traverse(parent)

        anon_counter = 0
        for anon_class in anon_classes:
            if anon_class.start_point.row <= node.start_point.row:
                anon_counter += 1
        return anon_counter

    def get_qualified_class_name(self, node, package_name: str) -> str:
        """
        Follow the parent of the node to find all class_declarations
        and enum_declarations,
        collecting their names in order from outer to inner.
        Example: packageName.Outer$Inner$LocalClass, etc.
        """

        class_names = []
        current = node.parent
        while current is not None:
            if current.type in [
                "class_declaration",
                "enum_declaration",
                "interface_declaration",
            ]:
                name_node = current.child_by_field_name("name")
                if name_node:
                    class_names.append(name_node.text.decode())
            # Note: We do not care about anonymous classes for now
            elif current.type == "object_creation_expression":
                if any(child.type == "class_body" for child in current.children):
                    anon_counter = self.count_anon_siblings(current)
                    class_names.append(f"{anon_counter}")

            current = current.parent

        class_names.reverse()
        qualified = "$".join(class_names)
        if package_name:
            qualified = package_name + "." + qualified
        return qualified

    def get_parameter_types(self, params_node) -> str:
        param_types = []
        if params_node:
            for child in params_node.children:
                if child.type == "formal_parameter":
                    type_node = child.child_by_field_name("type")
                    if type_node:
                        param_types.append(type_node.text.decode())
        return f"({', '.join(param_types)})"

    async def parse_file(
        self, file_path: Path
    ) -> Tuple[Dict[str, InternalFunctionRes], Dict[str, List[InternalFunctionRes]]]:
        tree_sitter_lang = get_language(self.language)
        parser = Parser(tree_sitter_lang)

        with open(file_path, "rb") as f:
            source_code = f.read()

        tree = parser.parse(source_code)
        package_query = tree_sitter_lang.query(
            "(package_declaration (_) @package_name)"
        )

        capture = package_query.captures(tree.root_node)
        if capture:
            package_name = capture["package_name"][0].text.decode()
        else:
            package_name = ""

        query = tree_sitter_lang.query(self.tree_sitter_query)

        hash_mapped_data = {}
        set_mapped_data = defaultdict(list)
        for _, match in query.matches(tree.root_node):
            if "method_declaration" in match:  # Regular method
                return_type = match["return_type"][0].text.decode()
                method_name = match["method_name"][0].text.decode()
                method_node = match["method_declaration"][0]

                qualified_class = self.get_qualified_class_name(
                    method_node, package_name
                )

                params_node = match["params"][0]
                type_only_params = self.get_parameter_types(params_node)
                params = match["params"][0].text.decode()

                simple_signature = (
                    f"{return_type} {qualified_class}.{method_name}{params}"
                )
                type_only_param_signature = (
                    f"{return_type} {qualified_class}.{method_name}{type_only_params}"
                )
                method = method_node

            elif "constructor_declaration" in match:  # Constructor
                constructor_name = match["constructor_name"][0].text.decode()
                constructor_node = match["constructor_declaration"][0]

                qualified_class = self.get_qualified_class_name(
                    constructor_node, package_name
                )

                params_node = match["constructor_params"][0]
                type_only_params = self.get_parameter_types(params_node)
                params = match["constructor_params"][0].text.decode()

                # Constructor has no return type, using the class name instead
                simple_signature = f"{qualified_class}.{constructor_name}{params}"
                type_only_param_signature = (
                    f"{qualified_class}.{constructor_name}{type_only_params}"
                )
                method = constructor_node
                method_name = constructor_name
            else:
                continue  # Skip if neither method nor constructor

            data = InternalFunctionRes(
                func_name=simple_signature,
                file_path=str(file_path),
                start_line=method.start_point.row + 1,
                end_line=method.end_point.row + 1,
                func_body=method.text.decode(),
                type_only_func_name=type_only_param_signature,
            )

            hash_mapped_data[simple_signature] = data
            set_mapped_data[method_name].append(data)

        return hash_mapped_data, set_mapped_data
