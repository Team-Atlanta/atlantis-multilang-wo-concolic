import datetime
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from mlla.agents.blobgen_agent.nodes.payload_generation import (
    execute_payload_code,
    generate_payload,
    verify_payload_code,
)
from mlla.utils.attribute_cg import AttributeCG, AttributeFuncInfo, LocationInfo
from mlla.utils.code_tags import (
    END_PAYLOAD_CODE_TAG,
    END_PAYLOAD_DESC_TAG,
    PAYLOAD_CODE_TAG,
    PAYLOAD_DESC_TAG,
)
from mlla.utils.llm import LLM

from .dummy_context import DummyContext

# Test data
MOCK_CG_CODE = """
void test_function() {
    char* buf = malloc(10);
    /*BUG_HERE*/
    strcpy(buf, input);
}
"""

MOCK_LLM_RESPONSE = f"""
{PAYLOAD_DESC_TAG}
This payload triggers a buffer overflow by:
1. Creating a large input string
2. Copying it to a small buffer
3. Overflowing into adjacent memory
{END_PAYLOAD_DESC_TAG}

{PAYLOAD_CODE_TAG}
def create_payload():
    # Create a buffer larger than the target
    payload = b"A" * 20
    return payload
{END_PAYLOAD_CODE_TAG}
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
    """Create a mock LLM that returns our test response."""
    llm = Mock()
    llm.invoke.return_value = [Mock(content=MOCK_LLM_RESPONSE)]
    # Configure ask_and_repeat_until to return a tuple of (code, desc, blob)
    llm.ask_and_repeat_until.return_value = (
        (
            "def create_payload():\n    # Create a buffer larger than the target\n   "
            ' payload = b"A" * 20\n    return payload'
        ),
        "This payload triggers a buffer overflow",
        b"A" * 20,
    )
    return llm


@pytest.fixture
def mock_attr_cg():
    """Create a mock AttributeCG for testing."""
    # Create a location info for the function
    location = LocationInfo(
        func_name="test_function",
        file_path="test_file.c",
        start_line=1,
        end_line=5,
    )

    # Create a function info with the location
    func_info = AttributeFuncInfo(
        func_location=location,
        func_body=MOCK_CG_CODE,
        children=[],
    )

    # Create the AttributeCG with the function info as root
    attr_cg = AttributeCG(
        name="test_cg",
        path="test_path",
        root_node=func_info,
        language="c",
    )

    return attr_cg


def test_execute_payload_code_success():
    """Test successful payload blob generation."""
    script = """
def create_payload():
    return b"test_payload"
"""
    blob = execute_payload_code(script)
    assert blob == b"test_payload"


def test_execute_payload_code_with_imports():
    """Test payload generation with imports."""
    script = """
def create_payload():
    import struct
    import base64

    # Create a simple payload with struct packing
    data = struct.pack("<I", 12345)
    encoded = base64.b64encode(data)
    return encoded
"""
    blob = execute_payload_code(script)
    assert len(blob) > 0
    assert blob == b"OTAAAA=="


def test_execute_payload_code_error_handling():
    """Test payload execution error handling."""
    # Test with syntax error
    invalid_script = """
def create_payload()
    return b"invalid syntax"
"""
    with pytest.raises(ValueError) as exc_info:
        execute_payload_code(invalid_script)
    assert "Payload code execution error" in str(exc_info.value)

    # Test with wrong return type
    wrong_type_script = """
def create_payload():
    return "string instead of bytes"
"""
    with pytest.raises(ValueError) as exc_info:
        execute_payload_code(wrong_type_script)
    assert "Payload code execution error" in str(exc_info.value)


def test_verify_payload_code_validation():
    """Test verify_payload_code validation."""

    # Test with invalid function signature
    invalid_signature_response = f"""
{PAYLOAD_DESC_TAG}
This is a test description
{END_PAYLOAD_DESC_TAG}

{PAYLOAD_CODE_TAG}
def create_payload(param):
    return b"test"
{END_PAYLOAD_CODE_TAG}
"""
    message = AIMessage(content=invalid_signature_response)
    with pytest.raises(ValueError) as exc_info:
        verify_payload_code(message)
    assert "No valid create_payload() function found" in str(exc_info.value)


@pytest.fixture
def mock_state(mock_llm, mock_attr_cg, global_context):
    """Create a mock state for testing generate_payload."""
    return {
        # Basic information
        "harness_name": "test_harness",
        "sanitizer": "address",
        "selected_sanitizers": ["AddressSanitizer"],
        "cg_name": "test_cg",
        # Core components
        "attr_cg": mock_attr_cg,
        "bit": None,
        # Added in preprocess
        "gc": global_context,
        "llm": mock_llm,
        "iter_cnt": 0,
        "cp_name": "test_cp",
        # Current state
        "current_payload": {},
        # Timestamps
        "start_time": datetime.datetime.now(),
        # Messages
        "messages": [HumanMessage(content="Test message")],
        # Status
        "status": "pending",
        "error": {"phase": "", "status": "", "details": ""},
        # Results
        "payload_dict": {},
        "crashed_blobs": {},
    }


@pytest.mark.skip(reason="store_artifact() is moved to collect_coverage()")
def test_generate_payload(mock_state):
    """Test the generate_payload function."""
    # Configure the mock LLM to return a valid response
    mock_llm = mock_state["llm"]
    mock_response = AIMessage(content=MOCK_LLM_RESPONSE)
    mock_llm.ask_and_repeat_until.return_value = (
        mock_response,
        ("def create_payload():\n    return b'test'", "Test description", b"test"),
    )

    # Mock the store_artifact function to avoid file system operations
    with patch(
        "mlla.agents.blobgen_agent.nodes.payload_generation.store_artifact"
    ) as mock_store_blob:
        # Call the function
        result = generate_payload(mock_state)

        # Verify the result
        assert result["status"] == "success"
        assert "current_payload" in result
        assert "code" in result["current_payload"]
        assert "desc" in result["current_payload"]
        assert "blob" in result["current_payload"]
        assert "blob_hash" in result["current_payload"]

        # Verify the store_artifact was called
        mock_store_blob.assert_called_once()


def test_generate_payload_failure(mock_state):
    """Test generate_payload with a failure in LLM response."""
    # Configure the mock LLM to raise an exception
    mock_llm = mock_state["llm"]
    mock_llm.ask_and_repeat_until.return_value = None

    # Call the function
    result = generate_payload(mock_state)

    # Verify the result indicates failure
    assert result["status"] == "failed"
    assert "error" in result
    assert result["error"]["phase"] == "create"
    assert result["error"]["status"] == "failed"
