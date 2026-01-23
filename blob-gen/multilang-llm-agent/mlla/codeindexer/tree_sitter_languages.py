import tree_sitter_c as c
import tree_sitter_cpp as cpp
import tree_sitter_java as java
import tree_sitter_python as python
from tree_sitter import Language, Parser


def get_language(language: str) -> Language:
    if language == "c":
        return Language(c.language())
    elif language == "java":
        return Language(java.language())
    elif language == "python":
        return Language(python.language())
    elif language == "cpp":
        return Language(cpp.language())
    else:
        raise ValueError(f"Language {language} not supported")


def get_parser(language: str) -> Parser:
    parser = Parser(get_language(language))
    return parser
