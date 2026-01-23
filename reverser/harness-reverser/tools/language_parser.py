from langchain.schema import Document
from langchain.document_loaders import Blob
from langchain_community.document_loaders.parsers import LanguageParser
from langchain_community.document_loaders.parsers.language.language_parser import LANGUAGE_EXTENSIONS 
from langchain_community.document_loaders.parsers.language.tree_sitter_segmenter import TreeSitterSegmenter 
from langchain_community.document_loaders.parsers.language.csharp import CSharpSegmenter
from langchain_community.document_loaders.parsers.language.perl import PerlSegmenter

from tree_sitter import Language, Parser

# LanguageParser is based on tree-sitter-languages which is out-of-date.
# (https://github.com/grantjenks/py-tree-sitter-languages/issues/64#issuecomment-2192682886)
#
# This is a hack to make the tree_sitter_language_pack work with the LanguageParser.
import tree_sitter_language_pack
import sys
sys.modules["tree_sitter_languages"] = tree_sitter_language_pack
# End of hack

# Start of PyCapsule patch
#
# Recent tree-sitter is using PyCapsule instead of int for TSLanguage *.
# (https://github.com/Goldziher/tree-sitter-language-pack/releases/tag/v0.4.0)
# (https://github.com/Goldziher/tree-sitter-language-pack/pull/13)

# tree-sitter-c-sharp 0.23.1 is returning PyCapsule.

from tree_sitter_language_pack import SupportedLanguage

import ctypes

ctypes.pythonapi.PyCapsule_GetName.argtypes = [ctypes.py_object]
ctypes.pythonapi.PyCapsule_GetName.restype = ctypes.c_char_p

ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p

def get_language(language_name: SupportedLanguage) -> Language:
    binding = tree_sitter_language_pack.get_binding(language_name)
    if isinstance(binding, int):
        return Language(binding)
    elif binding.__class__.__name__ == "PyCapsule":
        name = ctypes.pythonapi.PyCapsule_GetName(binding)
        pointer = ctypes.pythonapi.PyCapsule_GetPointer(binding, name)
        return Language(pointer)
    else:
        return Language(binding)

tree_sitter_language_pack.get_language = get_language
# End of PyCapsule patch

from typing import Iterator, List
from pathlib import Path

# TODO: Change tree-sitter query to search for function name
class SafeLanguageParser(LanguageParser):
    EXTRA_C_LANGUAGE_EXTENSIONS = ["cc", "cxx", "C", "h"]

    def lazy_parse(self, blob: Blob) -> Iterator[Document]:
        ext = None
        if isinstance(blob.source, str):
            ext = Path(blob.source).suffix.lstrip(".")

        if self.language is None and ext is not None:
            self.language = LANGUAGE_EXTENSIONS.get(ext)

        languages = []
        if self.language:
            languages = [self.language]
        elif ext in self.EXTRA_C_LANGUAGE_EXTENSIONS:
            languages = ["c", "cpp"]
        # TODO: Ask LLM
        # else:

        for language in languages:
            self.language = language
            try:
                for doc in super().lazy_parse(blob):
                    yield doc
            # FIXME: Catch all Exceptions
            except UnicodeDecodeError:
                yield Document("")

class SafeTreeSitterSegmenter(TreeSitterSegmenter):
    def is_valid(self: TreeSitterSegmenter) -> bool:
        return True

    def extract_functions_classes(self: TreeSitterSegmenter) -> List[str]:
        language = self.get_language()
        query = language.query(self.get_chunk_query())

        parser = self.get_parser()
        tree = parser.parse(bytes(self.code, encoding="UTF-8"))
        captures = query.captures(tree.root_node)

        processed_lines = set()
        chunks = []

        for _, nodes in captures.items():
            for node in nodes:
                start_line = node.start_point[0]
                end_line = node.end_point[0]
                lines = list(range(start_line, end_line + 1))

                if any(line in processed_lines for line in lines):
                    continue

                processed_lines.update(lines)
                if node.text is not None:
                    chunk_text = node.text.decode("UTF-8")
                    chunks.append(chunk_text)

        return chunks

    def simplify_code(self: TreeSitterSegmenter) -> str:
        language = self.get_language()
        query = language.query(self.get_chunk_query())

        parser = self.get_parser()
        tree = parser.parse(bytes(self.code, encoding="UTF-8"))
        processed_lines = set()

        simplified_lines = self.source_lines[:]
        for _, nodes in query.captures(tree.root_node).items():
            for node in nodes:
                start_line = node.start_point[0]
                end_line = node.end_point[0]

                lines = list(range(start_line, end_line + 1))
                if any(line in processed_lines for line in lines):
                    continue

                simplified_lines[start_line] = self.make_line_comment(
                    f"Code for: {self.source_lines[start_line]}"
                )

                for line_num in range(start_line + 1, end_line + 1):
                    simplified_lines[line_num] = None  # type: ignore

                processed_lines.update(lines)

        return "\n".join(line for line in simplified_lines if line is not None)

    def get_parser(self: TreeSitterSegmenter) -> Parser:
        from tree_sitter import Parser

        parser = Parser(self.get_language())
        return parser

TreeSitterSegmenter.is_valid = SafeTreeSitterSegmenter.is_valid
TreeSitterSegmenter.extract_functions_classes = SafeTreeSitterSegmenter.extract_functions_classes
TreeSitterSegmenter.simplify_code = SafeTreeSitterSegmenter.simplify_code
TreeSitterSegmenter.get_parser = SafeTreeSitterSegmenter.get_parser

class SafeCSharpSegmenter(CSharpSegmenter):
    def get_language(self: CSharpSegmenter) -> Language:
        return tree_sitter_language_pack.get_language("csharp")

CSharpSegmenter.get_language = SafeCSharpSegmenter.get_language

class SafePerlSegmenter(PerlSegmenter):
    def get_chunk_query(self: PerlSegmenter) -> str:
        return """
[
    (subroutine_declaration_statement) @subroutine
]
"""

PerlSegmenter.get_chunk_query = SafePerlSegmenter.get_chunk_query