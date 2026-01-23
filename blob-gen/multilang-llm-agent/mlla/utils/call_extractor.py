from pathlib import Path
from typing import Optional

from loguru import logger
from tree_sitter import Node

from mlla.codeindexer.tree_sitter_languages import get_language, get_parser

LANGUAGE_QUERIES = {
    "c": "(call_expression function: (identifier) @callee_name)",
    "cpp": (
        """
(call_expression function: (identifier) @callee_name)
(call_expression function:(field_expression field: (field_identifier) @callee_name))
(call_expression function: (qualified_identifier name: (identifier) @callee_name))
"""
    ),
    "java": (
        """
(method_invocation name: (identifier) @callee_name)
(object_creation_expression type: (type_identifier) @callee_name)
(explicit_constructor_invocation constructor: (this) @callee_name)
(explicit_constructor_invocation constructor: (super) @callee_name)
"""
    ),
}

_LANGUAGE_TO_EXTENTION = {
    "c": [".c", ".h"],
    "java": [".java"],
    "cpp": [".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx"],
    "_ignore": [".txt", ".md"],
}

EXTENTION_TO_LANGUAGE = {
    ext: lang for lang, exts in _LANGUAGE_TO_EXTENTION.items() for ext in exts
}


def find_node_by_func_body(node: Node, func_body: str) -> Node:
    """Find the node by the function body."""
    if node.text.decode("utf8").strip() == func_body.strip():
        return node
    for child in node.children:
        result = find_node_by_func_body(child, func_body)
        if result:
            return result
    return None


def get_all_calls(
    file_path: Optional[str], func_body: Optional[str]
) -> tuple[list[Node], bool]:  # return value: (list of nodes, from_file_path)
    """ """
    if not file_path:
        raise ValueError("File path is empty.")
    if not func_body:
        raise ValueError("Function body is empty.")

    ext = Path(file_path).suffix
    if ext not in EXTENTION_TO_LANGUAGE:
        logger.info("[Call_extractor] Unsupported file extension: %s", ext)
        return [], False
    language = EXTENTION_TO_LANGUAGE[ext]
    if language == "_ignore":
        return [], False

    parser = get_parser(language)
    lang = get_language(language)

    tree = parser.parse(func_body.encode())
    node = tree.root_node
    from_file_path = False

    if tree.root_node.has_error:
        # logger.debug("Tree has error, trying to find the node by the function body")
        with open(file_path, "rb") as f:
            file_content = f.read()
            tree = parser.parse(file_content)
            _node = find_node_by_func_body(tree.root_node, func_body)
            if _node:
                node = _node
                from_file_path = True

    query = lang.query(LANGUAGE_QUERIES[language])
    captures = query.captures(node)

    # Extract nodes from the captures results
    if not captures:
        return [], from_file_path

    function_calls = [
        # node.text.decode("utf8")
        node
        for node in captures["callee_name"]
        if node.text
    ]
    # Node can be used to get function definition through LSP
    return function_calls, from_file_path
