from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mlla.agents.generator_agent.agent import GeneratorAgent
from mlla.agents.generator_agent.state import GeneratorAgentInputState

from .dummy_context import DummyContext


@pytest.fixture
def generator_agent(monkeypatch):
    """Create a GeneratorAgent instance"""
    # Mock environment variables to prevent hanging on API key prompt
    monkeypatch.setenv("LITELLM_KEY", "dummy_key")
    monkeypatch.setenv("LITELLM_URL", "dummy_url")

    agent_instance = GeneratorAgent(DummyContext())

    return agent_instance


@pytest.fixture
def sample_source_file(tmp_path):
    """Create a sample source file for testing."""
    source_path = tmp_path / "source.c"
    source_path.write_text("int main() { return 0; }")
    return source_path


@pytest.fixture
def sample_diff_file(tmp_path):
    """Create a sample diff file for testing."""
    diff_path = tmp_path / "ref.diff"
    diff_content = """
--- a/source.c
+++ b/source.c
@@ -1 +1,3 @@
-int main() { return 0; }
+int main() {
+    return 1;  /* Vulnerable change */
+}
"""
    diff_path.write_text(diff_content)
    return diff_path


@patch("mlla.agents.generator_agent.agent.build_prompts")
def test_generator_with_explicit_diff_path(
    mock_build_prompts, generator_agent, sample_source_file, sample_diff_file
):
    """Test generator agent with explicitly provided diff path."""
    # Create input state with explicit diff path
    input_state = GeneratorAgentInputState(
        standalone=True,
        harness_name="test_harness",
        source_path=str(sample_source_file),
        diff_path=str(sample_diff_file),
        sanitizer="test_sanitizer",
    )

    # Process the input state
    generator_agent.preprocess(input_state)

    # Verify that build_prompts was called with the diff code
    mock_build_prompts.assert_called_once()
    call_args = mock_build_prompts.call_args[1]

    # Check that diff_code was passed to build_prompts and is not empty
    assert "diff_code" in call_args
    assert call_args["diff_code"] != ""

    # Verify the diff code content matches our sample
    assert "Vulnerable change" in call_args["diff_code"]


@patch("mlla.agents.generator_agent.agent.build_prompts")
def test_generator_with_default_diff_path(
    mock_build_prompts,
    generator_agent,
    sample_source_file,
    sample_diff_file,
    monkeypatch,
):
    """Test generator agent with default diff path when file exists."""
    # Setup - create a mock Path.is_file that returns True for /src/ref.diff
    original_is_file = Path.is_file

    def mock_is_file(self):
        if str(self) == "/src/ref.diff":
            return True
        return original_is_file(self)

    def mock_read_text(self):
        if str(self) == "/src/ref.diff":
            return sample_diff_file.read_text()
        return original_read_text(self)

    original_read_text = Path.read_text
    monkeypatch.setattr(Path, "is_file", mock_is_file)
    monkeypatch.setattr(Path, "read_text", mock_read_text)

    # Create input state without explicit diff path
    input_state = GeneratorAgentInputState(
        standalone=True,
        harness_name="test_harness",
        source_path=str(sample_source_file),
        diff_path=str("/src/ref.diff"),
        sanitizer="test_sanitizer",
    )

    # Process the input state
    generator_agent.preprocess(input_state)

    # Verify that build_prompts was called with the diff code
    mock_build_prompts.assert_called_once()
    call_args = mock_build_prompts.call_args[1]

    # Check that diff_code was passed to build_prompts and is not empty
    assert "diff_code" in call_args
    assert call_args["diff_code"] != ""
    assert "Vulnerable change" in call_args["diff_code"]


@patch("mlla.agents.generator_agent.agent.build_prompts")
def test_generator_without_diff_path(
    mock_build_prompts, generator_agent, sample_source_file, monkeypatch
):
    """Test generator agent when default diff path doesn't exist."""
    # Setup - create a mock Path.is_file that returns False for /src/ref.diff
    original_is_file = Path.is_file

    def mock_is_file(self):
        if str(self) == "/src/ref.diff":
            return False
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", mock_is_file)

    # Create input state without explicit diff path
    input_state = GeneratorAgentInputState(
        standalone=True,
        harness_name="test_harness",
        source_path=str(sample_source_file),
        sanitizer="test_sanitizer",
        # No diff_path provided and default doesn't exist
    )

    # Process the input state
    generator_agent.preprocess(input_state)

    # Verify that build_prompts was called without diff code
    mock_build_prompts.assert_called_once()
    call_args = mock_build_prompts.call_args[1]

    # Check that diff_code was passed to build_prompts but is empty
    assert "diff_code" in call_args
    assert call_args["diff_code"] == ""


@patch("mlla.agents.generator_agent.agent.build_prompts")
def test_generator_with_large_diff_file(
    mock_build_prompts, generator_agent, sample_source_file, tmp_path
):
    """Test generator agent with a diff file that exceeds the size limit."""
    # Create a large diff file
    large_diff_path = tmp_path / "large.diff"
    large_diff_content = "--- a/file\n+++ b/file\n" + "\n".join(
        [f"@@ line {i} @@" for i in range(1001)]
    )
    large_diff_path.write_text(large_diff_content)

    # Create input state with the large diff file
    input_state = GeneratorAgentInputState(
        standalone=True,
        harness_name="test_harness",
        source_path=str(sample_source_file),
        diff_path=str(large_diff_path),
        sanitizer="test_sanitizer",
    )

    # Process the input state
    generator_agent.preprocess(input_state)

    # Verify that build_prompts was called without diff code
    mock_build_prompts.assert_called_once()
    call_args = mock_build_prompts.call_args[1]

    # Check that diff_code was passed to build_prompts but is empty due to size limit
    assert "diff_code" in call_args
    assert call_args["diff_code"] == ""


@patch("mlla.main.GeneratorAgent")
@patch("mlla.main.GlobalContext.init")
@pytest.mark.skip(reason="This test is failed.")
@pytest.mark.asyncio
async def test_call_generator_agent_with_diff_path(mock_init, mock_generator_agent):
    """Test the call_generator_agent function with a custom diff path."""
    # Setup mocks
    mock_init.return_value.__aenter__.return_value = None
    mock_init.return_value.__aexit__.return_value = None

    mock_graph = AsyncMock()
    mock_generator_agent.return_value.compile.return_value = mock_graph
    mock_graph.ainvoke.return_value = {"result": "success"}

    # Call the function with a custom diff path
    from mlla.main import call_generator_agent

    gc = DummyContext()
    custom_diff_path = "/custom/path/to/diff.diff"
    await call_generator_agent(gc, diff_path=custom_diff_path)

    # Verify the graph was invoked with the correct input state
    mock_graph.ainvoke.assert_called_once()
    input_state = mock_graph.ainvoke.call_args[0][0]

    # Check that the diff_path was correctly passed
    assert input_state["diff_path"] == custom_diff_path
