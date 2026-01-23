import os
from unittest.mock import MagicMock, Mock

import pytest
from langchain_core.messages import SystemMessage

from mlla.agents.blobgen_agent.nodes.select_sanitizer import (
    generate_sanitizer_selection,
)
from mlla.utils.attribute_cg import AttributeCG
from mlla.utils.code_tags import END_SAN_TAG, SAN_TAG
from mlla.utils.llm import LLM

from .dummy_context import DummyContext

# Test data
MOCK_JAZZER_CODE = """
def process_input(user_input):
    os.system(f"process {user_input}")
    cursor.execute(f"SELECT * FROM users WHERE id = {user_input}")
    with open(user_input) as f:
        data = f.read()
"""

MOCK_ADDRESS_CODE = """
void process_data(char* data, size_t size) {
    char stack_buf[10];
    strcpy(stack_buf, data);  // Potential stack overflow

    char* heap_buf = malloc(10);
    memcpy(heap_buf, data, size);  // Potential heap overflow
    free(heap_buf);
    heap_buf[0] = 'x';  // Use after free
}
"""

MOCK_JAZZER_RESPONSE = f"""
Code Analysis Summary:
- Found multiple security-critical operations involving user input
- No input validation or sanitization present

Vulnerability Assessment:
1. File path traversal risk in open() call
2. OS command injection risk in os.system()
3. SQL injection risk in execute()

{SAN_TAG}jazzer.FilePathTraversal,jazzer.OsCommandInjection,jazzer.SqlInjection{END_SAN_TAG}
"""

MOCK_ADDRESS_RESPONSE = f"""
Code Analysis Summary:
- Multiple memory operations without bounds checking
- Use of freed memory

Vulnerability Assessment:
1. Stack buffer overflow in strcpy()
2. Heap buffer overflow in memcpy()
3. Use-after-free after free()

{SAN_TAG}address.stack-buffer-overflow,address.heap-buffer-overflow,address.heap-use-after-free{END_SAN_TAG}
"""


@pytest.fixture
def global_context():
    """Create a dummy global context for testing."""
    return DummyContext(no_llm=True)


@pytest.fixture
def llm(global_context):
    """Create an LLM instance for testing."""
    return LLM(
        model="gpt-4.1-nano",
        config=global_context,
    )


@pytest.fixture
def mock_llm():
    """Create a mock LLM that returns our test responses."""
    llm = Mock()
    llm.ask_and_repeat_until = Mock()

    def get_mock_response(messages):
        # Extract sanitizer type from possible_sanitizers in the system message
        system_msg = next(msg for msg in messages if isinstance(msg, SystemMessage))
        if "jazzer" in system_msg.content:
            return Mock(content=MOCK_JAZZER_RESPONSE)
        elif "address" in system_msg.content:
            return Mock(content=MOCK_ADDRESS_RESPONSE)
        return None

    def mock_ask_and_repeat(checker, messages, default, max_retries=3):
        return checker(get_mock_response(messages))

    llm.ask_and_repeat_until.side_effect = mock_ask_and_repeat
    return llm


def create_mock_attr_cg(code: str) -> AttributeCG:
    """Create a mock AttributeCG object for testing."""
    mock_attr_cg = MagicMock(spec=AttributeCG)
    mock_attr_cg.get_annotated_function_bodies.return_value = code
    return mock_attr_cg


def test_select_sanitizer_jazzer(mock_llm):
    """Test jazzer sanitizer vulnerability detection."""
    mock_attr_cg = create_mock_attr_cg(MOCK_JAZZER_CODE)
    result = generate_sanitizer_selection(
        mock_llm,
        ["jazzer"],
        mock_attr_cg,
    )

    assert len(result) == 3
    assert "jazzer.FilePathTraversal" in result
    assert "jazzer.OsCommandInjection" in result
    assert "jazzer.SqlInjection" in result
    mock_llm.ask_and_repeat_until.assert_called_once()


def test_select_sanitizer_address(mock_llm):
    """Test address sanitizer vulnerability detection."""
    mock_attr_cg = create_mock_attr_cg(MOCK_ADDRESS_CODE)
    result = generate_sanitizer_selection(
        mock_llm,
        ["address"],
        mock_attr_cg,
    )

    assert len(result) == 3, result
    assert "address.stack-buffer-overflow" in result
    assert "address.heap-buffer-overflow" in result
    assert "address.heap-use-after-free" in result
    mock_llm.ask_and_repeat_until.assert_called_once()


def test_select_sanitizer_with_context(mock_llm):
    """Test sanitizer selection with additional context."""

    # Override mock response for this specific test
    def mock_sql_response(checker, _, default, max_retries=3):
        response = Mock(
            content=(
                "Code Analysis Summary:\n"
                "- SQL query with unsanitized input\n"
                "- Context indicates direct user input usage\n\n"
                "Vulnerability Assessment:\n"
                "1. SQL injection risk in execute() with user input\n\n"
                f"{SAN_TAG}jazzer.SqlInjection{END_SAN_TAG}"
            )
        )
        return checker(response)

    mock_llm.ask_and_repeat_until.side_effect = mock_sql_response

    mock_attr_cg = create_mock_attr_cg("cursor.execute(query)")
    result = generate_sanitizer_selection(
        mock_llm,
        ["jazzer"],
        mock_attr_cg,
        context="The query variable comes from user input without sanitization",
    )

    assert result == ["jazzer.SqlInjection"]
    mock_llm.ask_and_repeat_until.assert_called_once()


def test_select_sanitizer_error_cases(mock_llm):
    """Test various error cases."""
    mock_attr_cg = create_mock_attr_cg("print('test')")

    # Test invalid sanitizer type
    def mock_invalid_type_response(checker, _, default, max_retries=3):
        response = Mock(
            content=(
                "Code Analysis Summary:\n"
                "- Simple print statement\n"
                "- No security concerns\n\n"
                f"{SAN_TAG}invalid.type{END_SAN_TAG}"
            )
        )
        return checker(response)

    mock_llm.ask_and_repeat_until.side_effect = mock_invalid_type_response
    with pytest.raises(ValueError, match="Invalid sanitizer suggested"):
        generate_sanitizer_selection(mock_llm, ["jazzer"], mock_attr_cg)

    # Test missing tags
    def mock_missing_tags_response(checker, _, default, max_retries=3):
        response = Mock(
            content=(
                "Code Analysis Summary:\n"
                "- Simple print statement\n"
                "- No security concerns"
            )
        )
        return checker(response)

    mock_llm.ask_and_repeat_until.side_effect = mock_missing_tags_response
    with pytest.raises(ValueError, match=f"No {SAN_TAG} tags found"):
        generate_sanitizer_selection(mock_llm, ["jazzer"], mock_attr_cg)

    # Test invalid format
    def mock_invalid_format_response(checker, _, default, max_retries=3):
        response = Mock(
            content=(
                "Code Analysis Summary:\n"
                "- Simple print statement\n"
                "- No security concerns\n\n"
                f"{SAN_TAG}jazzer-sql{END_SAN_TAG}"
            )
        )
        return checker(response)

    mock_llm.ask_and_repeat_until.side_effect = mock_invalid_format_response
    with pytest.raises(ValueError, match="Invalid output format"):
        generate_sanitizer_selection(mock_llm, ["jazzer"], mock_attr_cg)


@pytest.mark.skipif(
    not os.getenv("RUN_LLM_TESTS"),
    reason="LLM tests are disabled. Set RUN_LLM_TESTS=1 to enable.",
)
def test_real_llm_integration(llm):
    """Integration test using real LLM.

    This test requires real LLM access and is skipped by default.
    To run this test, set the RUN_LLM_TESTS environment variable:

    RUN_LLM_TESTS=1 pytest tests/test_sanitizer_selector.py -v

    The test will try up to 3 times since LLM responses can be inconsistent.
    If any attempt succeeds, the test passes.
    """
    max_attempts = 3
    last_error = None

    for attempt in range(max_attempts):
        try:
            # Test jazzer sanitizer
            mock_attr_cg = create_mock_attr_cg(MOCK_JAZZER_CODE)
            results = generate_sanitizer_selection(
                llm,
                ["jazzer"],
                mock_attr_cg,
            )
            assert len(results) > 0
            # DK: Should we check the vuln type as well?
            assert all("jazzer." in r for r in results)

            # Test address sanitizer
            mock_attr_cg = create_mock_attr_cg(MOCK_ADDRESS_CODE)
            results = generate_sanitizer_selection(
                llm,
                ["address"],
                mock_attr_cg,
            )
            assert len(results) > 0
            # DK: Should we check the vuln type as well?
            assert all("address." in r for r in results)
            return  # Test passed, exit early

        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:  # Don't log on last attempt
                continue

    # If we get here, all attempts failed
    raise last_error


if __name__ == "__main__":
    pytest.main()
