import os
import tempfile
from unittest.mock import patch

import pytest

from mlla.utils.coverage import annotate_funcs_with_coverage


def mock_instrument(code, start_line):
    """Mock implementation of instrument_line that matches the actual implementation."""
    lines = code.split("\n")
    numbered_lines = [f"[{start_line + i}]: {line}" for i, line in enumerate(lines)]
    return "\n".join(numbered_lines), start_line + len(lines) - 1


def extract_visited_line_numbers(result):
    """Helper function to extract line numbers that have @VISITED annotations."""
    result_lines = result.split("\n")
    visited_line_numbers = []
    for line in result_lines:
        if "/* @VISITED */" in line and "[" in line and "]:" in line:
            line_num_str = line.split("[")[1].split("]:")[0]
            if line_num_str.isdigit():
                visited_line_numbers.append(int(line_num_str))
    return visited_line_numbers


def verify_instrument_line_call(mock_instrument_line, expected_start_line=1):
    """Helper function to verify instrument_line was called correctly."""
    mock_instrument_line.assert_called_once()
    call_args = mock_instrument_line.call_args[0]
    assert call_args[1] == expected_start_line


@pytest.fixture
def temp_source_file():
    """Fixture that creates a temporary source file with test content."""
    test_code = """line 1: first line
line 2: second line
line 3: third line
line 4: fourth line
line 5: fifth line
line 6: sixth line
line 7: seventh line
line 8: eighth line
line 9: ninth line
line 10: tenth line"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".java", delete=False) as f:
        f.write(test_code)
        temp_file_path = f.name

    yield temp_file_path

    # Cleanup
    os.unlink(temp_file_path)


@patch("mlla.utils.coverage.instrument_line")
def test_annotate_funcs_with_coverage_absolute_numbering(
    mock_instrument_line, temp_source_file
):
    """Test that annotate_funcs_with_coverage uses absolute line numbering."""
    mock_instrument_line.side_effect = mock_instrument

    coverage_info = {
        "testFunction": {
            "src": temp_source_file,
            "lines": [3, 5, 7],  # Lines 3, 5, 7 from original file (1-based)
        }
    }

    result = annotate_funcs_with_coverage(coverage_info, context_buffer=2)

    # Verify basic structure
    assert "<FUNCTION_INFO>" in result
    assert "testFunction" in result
    assert temp_source_file in result
    assert "/* @VISITED */" in result

    # Verify absolute line numbering is preserved
    visited_line_numbers = extract_visited_line_numbers(result)
    expected_visited_lines = [3, 5, 7]
    assert sorted(visited_line_numbers) == expected_visited_lines

    # Verify instrument_line was called with absolute numbering
    verify_instrument_line_call(mock_instrument_line, expected_start_line=1)


@patch("mlla.utils.coverage.instrument_line")
def test_multiple_functions_same_file(mock_instrument_line, temp_source_file):
    """Test that multiple functions from the same file share the same source."""
    mock_instrument_line.side_effect = mock_instrument

    coverage_info = {
        "function1": {"src": temp_source_file, "lines": [2, 4]},
        "function2": {"src": temp_source_file, "lines": [6, 8]},
    }

    result = annotate_funcs_with_coverage(coverage_info, context_buffer=1)

    # Verify both functions are present
    assert result.count("<FUNCTION_INFO>") == 2
    assert "function1" in result
    assert "function2" in result

    # Verify all covered lines are annotated
    visited_line_numbers = extract_visited_line_numbers(result)
    expected_visited_lines = [2, 4, 6, 8]
    unique_visited_lines = sorted(list(set(visited_line_numbers)))
    assert unique_visited_lines == expected_visited_lines

    # Verify instrument_line was called once per file (not per function)
    verify_instrument_line_call(mock_instrument_line, expected_start_line=1)


@patch("mlla.utils.coverage.instrument_line")
def test_edge_case_line_filtering(mock_instrument_line, temp_source_file):
    """Test that invalid line numbers are properly handled."""
    mock_instrument_line.side_effect = mock_instrument

    coverage_info = {
        "testFunction": {
            "src": temp_source_file,
            "lines": [1, 5, 10, 15, 20],  # Lines 15, 20 don't exist in 10-line file
        }
    }

    result = annotate_funcs_with_coverage(coverage_info, context_buffer=1)

    # Verify only valid lines are annotated
    visited_line_numbers = extract_visited_line_numbers(result)
    expected_visited_lines = [1, 5, 10]  # Lines 15, 20 filtered out
    assert sorted(visited_line_numbers) == expected_visited_lines

    verify_instrument_line_call(mock_instrument_line, expected_start_line=1)


@patch("mlla.utils.coverage.instrument_line")
def test_max_line_number_filtering(mock_instrument_line, temp_source_file):
    """Test that functions exceeding max_line_number are skipped with warning."""
    mock_instrument_line.side_effect = mock_instrument

    coverage_info = {
        "smallFunction": {
            "src": temp_source_file,
            "lines": [
                2,
                3,
            ],  # Small function: lines 2-3, with context_buffer=1 -> 3 lines total
        },
        "largeFunction": {
            "src": temp_source_file,
            "lines": [
                1,
                10,
            ],  # Large function: lines 1-10, with context_buffer=1 -> 11 lines total
        },
    }

    # Set max_line_number to 5, so largeFunction should be skipped
    with patch("mlla.utils.coverage.logger") as mock_logger:
        result = annotate_funcs_with_coverage(
            coverage_info, context_buffer=1, max_line_number=5
        )

        # Verify warning was logged for the large function
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        assert "largeFunction" in warning_call
        assert "11 lines exceeds max_line_number limit of 5" in warning_call

    # Verify only the small function is in the result
    assert "smallFunction" in result
    assert "largeFunction" not in result
    assert result.count("<FUNCTION_INFO>") == 1

    # Verify only small function lines are annotated
    visited_line_numbers = extract_visited_line_numbers(result)
    expected_visited_lines = [2, 3]  # Only from smallFunction
    assert sorted(visited_line_numbers) == expected_visited_lines
