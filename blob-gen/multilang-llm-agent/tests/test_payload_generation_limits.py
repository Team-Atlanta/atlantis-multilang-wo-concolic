from unittest.mock import patch

import pytest

from mlla.agents.blobgen_agent.nodes.payload_generation import execute_payload_code


def test_payload_size_limit():
    """Test that payloads larger than 1MB are truncated."""
    large_payload_code = """
def create_payload():
    # Generate 2MB of data
    return b'x' * (2 * 1024 * 1024)
"""
    # Use a direct approach without mocking
    with patch("mlla.utils.execute_llm_code.execute_python_script") as mock_execute:
        # Configure the mock to simulate successful execution
        # and write the large payload to the output file
        def side_effect(code, args):
            # Extract the output file path from args
            output_path = args[0]
            # Write a 2MB payload to the file
            with open(output_path, "wb") as f:
                f.write(b"x" * (2 * 1024 * 1024))
            # Return empty string to indicate no error
            return ""

        mock_execute.side_effect = side_effect

        # Execute the payload code
        blob = execute_payload_code(large_payload_code)

        # Verify the blob is truncated to 1MB
        assert len(blob) == 1024 * 1024


def test_memory_limit():
    """Test that memory-intensive payloads raise appropriate errors."""
    memory_heavy_code = """
def create_payload():
    # Try to allocate 2GB of memory
    big_list = [b'x' * 1024 for _ in range(2 * 1024 * 1024)]
    return b''.join(big_list)
"""
    with pytest.raises(ValueError) as exc_info:
        execute_payload_code(memory_heavy_code)
    assert "RESOURCE LIMIT ERROR" in str(
        exc_info.value
    ) or "Payload code execution error" in str(exc_info.value)


def test_valid_payload():
    """Test that normal payloads within limits work correctly."""
    valid_payload_code = """
def create_payload():
    # Generate 100KB of data
    return b'x' * (100 * 1024)
"""
    blob = execute_payload_code(valid_payload_code)
    assert len(blob) == 100 * 1024  # Should be unchanged
