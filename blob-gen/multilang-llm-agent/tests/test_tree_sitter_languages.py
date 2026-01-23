import pytest
from tree_sitter import Parser

from mlla.codeindexer.tree_sitter_languages import get_parser


def test_get_parser_returns_parser():
    parser = get_parser("java")
    assert isinstance(parser, Parser)


def test_get_parser_supported_languages():
    # Test all supported languages
    for lang in ["c", "java", "python"]:
        parser = get_parser(lang)
        assert isinstance(parser, Parser)


def test_get_parser_unsupported_language():
    with pytest.raises(ValueError, match="Language ruby not supported"):
        get_parser("ruby")
