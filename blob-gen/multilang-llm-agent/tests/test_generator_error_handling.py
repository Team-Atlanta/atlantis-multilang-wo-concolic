from unittest.mock import patch

import pytest

from mlla.agents.generator_agent.nodes.common import execute_generator


def test_execute_generator_success():
    """Test execute_generator with a successful generator."""
    # Simple generator that always succeeds
    generator_code = """
def generate(rnd):
    return b"test_payload_" + str(rnd.randint(1, 1000)).encode()
"""

    # Execute the generator
    blobs, errors = execute_generator(generator_code, seed_num=42, num_blobs=3)

    # Verify results
    assert len(blobs) == 3
    assert len(errors) == 0

    # Check that blobs contain the expected content
    for blob in blobs:
        assert blob.startswith(b"test_payload_")
        assert len(blob) > 12  # "test_payload_" + at least one digit


def test_execute_generator_all_errors():
    """Test execute_generator with a generator that always fails."""
    # Generator that always raises an exception
    generator_code = """
def generate(rnd):
    raise ValueError("Intentional test error")
"""

    # Execute the generator
    blobs, errors = execute_generator(generator_code, seed_num=42, num_blobs=3)

    # Verify results
    assert len(blobs) == 0
    assert len(errors) == 3

    # Check that errors contain the expected message
    for error in errors:
        assert "ValueError: Intentional test error" in error
        assert "Traceback" in error


def test_execute_generator_mixed_results():
    """Test execute_generator with a generator that sometimes fails."""
    # Generator that fails on even-numbered attempts
    generator_code = """
def generate(rnd):
    static_counter = getattr(generate, 'counter', 0)
    generate.counter = static_counter + 1

    if static_counter % 2 == 0:
        raise ValueError(f"Intentional error on attempt {static_counter}")

    return f"Success on attempt {static_counter}".encode()
"""

    # Execute the generator
    blobs, errors = execute_generator(generator_code, seed_num=42, num_blobs=4)

    # Verify results
    assert len(blobs) == 2
    assert len(errors) == 2

    # Check blob content
    for blob in blobs:
        assert blob.startswith(b"Success on attempt")

    # Check error content
    for error in errors:
        assert "ValueError: Intentional error on attempt" in error
        assert "Traceback" in error


def test_execute_generator_memory_limit_in_generator():
    """Test a generator that exceeds memory limits inside generate()."""
    # Generator that tries to allocate a large amount of memory inside generate()
    generator_code = """
def generate(rnd):
    # Try to allocate 2GB of memory using a list comprehension
    big_list = [b'x' * 1024 for _ in range(2 * 1024 * 1024)]
    return b''.join(big_list)
"""

    # Execute the generator
    blobs, errors = execute_generator(generator_code, seed_num=42, num_blobs=1)

    # Verify results
    assert len(blobs) == 0, "Expected no successful blobs"
    assert len(errors) == 1, "Expected one error"

    # Check that the error contains a memory error
    assert "MemoryError" in errors[0]


def test_execute_generator_memory_limit_in_script():
    """Test a script that exceeds memory limits outside generate()."""
    # Simple generator code
    generator_code = """
def generate(rnd):
    return b"test"
"""

    # Mock execute_python_script to simulate a memory error at the script level
    with patch(
        "mlla.agents.generator_agent.nodes.common.execute_python_script"
    ) as mock_execute:
        # Configure the mock to return a memory error
        error_msg = "MemoryError: Out of memory"
        mock_execute.return_value = error_msg

        # Execute the generator and expect a ValueError about memory limits
        with pytest.raises(ValueError) as excinfo:
            execute_generator(generator_code, seed_num=42, num_blobs=1)

        # Verify the error message
        assert "RESOURCE LIMIT ERROR" in str(excinfo.value)
        assert "Memory limit exceeded" in str(excinfo.value)


def test_execute_generator_syntax_error():
    """Test execute_generator with a generator that has syntax errors."""
    # Generator with syntax error
    generator_code = """
def generate(rnd):
    if True
        return b"This has a syntax error"
"""

    # Execute the generator and expect a ValueError about syntax error
    with pytest.raises(ValueError) as excinfo:
        execute_generator(generator_code, seed_num=42, num_blobs=1)

    # Verify the error message
    assert "Generator execution error" in str(excinfo.value)
    assert "SyntaxError" in str(excinfo.value)


def test_execute_generator_size_limit():
    """Test execute_generator with a generator that exceeds size limits."""
    # Generator that produces a payload larger than the 1MB limit
    generator_code = """
def generate(rnd):
    # Create a payload larger than 1MB
    return b"x" * (2 * 1024 * 1024)  # 2MB
"""

    # Execute the generator
    blobs, errors = execute_generator(generator_code, seed_num=42, num_blobs=1)

    # Verify results
    assert len(blobs) == 1
    assert len(errors) == 0

    # Check that the blob was truncated to 1MB
    assert len(blobs[0]) == 1024 * 1024  # 1MB
