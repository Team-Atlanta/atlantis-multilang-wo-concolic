"""
Test cases for large model fallback behavior in LLM class.
Tests the scenario where context limit errors trigger large model usage
and ensure the system continues using the large model with retry logic.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.rate_limiters import InMemoryRateLimiter

from mlla.utils.context import GlobalContext
from mlla.utils.llm import LLM


class MockContextLimitError(Exception):
    def __init__(self, message="input length and `max_tokens` exceed context limit"):
        super().__init__(message)


class MockTimeoutError(Exception):
    def __init__(self, message="Timeout error occurred"):
        super().__init__(message)


@pytest.fixture
def mock_config():
    config = Mock(spec=GlobalContext)
    config.api_key = "test-key"
    config.base_url = "https://test.com"
    config.openai_timeout = 30
    config.openai_max_retries = 3
    config.gemini_timeout = 30
    config.gemini_max_retries = 3
    config.atlanta_timeout = 30
    config.atlanta_max_retries = 3
    config.max_concurrent_async_llm_calls = 5
    config.general_callback = Mock()
    config.is_dev = False

    # Create proper rate limiter instances
    config.global_rate_limiter = InMemoryRateLimiter(requests_per_second=10)
    config.global_claude_rate_limiter = InMemoryRateLimiter(requests_per_second=5)

    return config


@pytest.fixture
def mock_llm_with_large_model(mock_config):
    """LLM with large context model enabled."""
    with patch("mlla.utils.llm.ChatOpenAI") as mock_chat_openai, patch(
        "mlla.utils.llm.ChatAnthropic"
    ), patch(
        "mlla.utils.llm.TOKEN_COSTS",
        {
            "gpt-4o-mini": {"max_input_tokens": 8192, "max_output_tokens": 4096},
            "gpt-4.1": {"max_input_tokens": 128000, "max_output_tokens": 4096},
            "gemini-2.5-flash": {
                "max_input_tokens": 1000000,
                "max_output_tokens": 8192,
            },
        },
    ):

        # Configure the mock to have proper max_tokens attribute
        mock_instance = Mock()
        mock_instance.max_tokens = None
        mock_chat_openai.return_value = mock_instance

        llm = LLM(
            model="gpt-4o-mini",
            config=mock_config,
            prepare_large_context_model=True,
        )

        llm.large_context_model = Mock()
        llm.large_context_model.invoke = Mock()
        llm.large_context_model.ainvoke = Mock()

        llm.large_context_model_fallback = Mock()
        llm.large_context_model_fallback.invoke = Mock()
        llm.large_context_model_fallback.ainvoke = Mock()

        return llm


@pytest.fixture
def mock_llm_without_large_model(mock_config):
    """LLM with large context model disabled."""
    with patch("mlla.utils.llm.ChatOpenAI") as mock_chat_openai, patch(
        "mlla.utils.llm.TOKEN_COSTS",
        {"gpt-4o-mini": {"max_input_tokens": 8192, "max_output_tokens": 4096}},
    ):

        # Configure the mock to have proper max_tokens attribute
        mock_instance = Mock()
        mock_instance.max_tokens = None
        mock_chat_openai.return_value = mock_instance

        llm = LLM(
            model="gpt-4o-mini",
            config=mock_config,
            prepare_large_context_model=False,
        )

        # Verify that large context models are not set
        assert llm.large_context_model is None
        assert llm.large_context_model_fallback is None

        return llm


# Tests for LLM with large context model enabled


def test_context_limit_triggers_large_model(mock_llm_with_large_model):
    """Test that context limit errors trigger large model usage."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()
        mock_llm_with_large_model.large_context_model.invoke.return_value = [
            AIMessage(content="Large model success")
        ]

        result = mock_llm_with_large_model.invoke(messages)

        assert mock_invoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1
        assert result == [AIMessage(content="Large model success")]


@patch("time.sleep")
def test_large_model_stickiness_with_retry(mock_sleep, mock_llm_with_large_model):
    """Core bug fix test: large model should remain sticky during retries."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()

        mock_llm_with_large_model.large_context_model.invoke.side_effect = [
            MockTimeoutError("Timeout error occurred"),
            [AIMessage(content="Large model success after retry")],
        ]

        result = mock_llm_with_large_model.invoke(messages)

        assert mock_invoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 2
        assert result == [AIMessage(content="Large model success after retry")]


def test_large_model_fallback_on_context_limit(mock_llm_with_large_model):
    """Test that large model fallback is used when primary large model fails."""
    messages = [HumanMessage(content="Test message")]

    mock_llm_with_large_model.large_context_model.invoke.side_effect = (
        MockContextLimitError()
    )
    mock_llm_with_large_model.large_context_model_fallback.invoke.return_value = [
        AIMessage(content="Fallback success")
    ]

    result = mock_llm_with_large_model.invoke_large_model(messages)

    assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1
    assert mock_llm_with_large_model.large_context_model_fallback.invoke.call_count == 1
    assert result == [AIMessage(content="Fallback success")]


def test_force_large_model_parameter(mock_llm_with_large_model):
    """Test that force_large_model parameter directly uses large model."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        mock_llm_with_large_model.large_context_model.invoke.return_value = [
            AIMessage(content="Large model response")
        ]

        result = mock_llm_with_large_model.invoke(messages, force_large_model=True)

        # Should not call regular model at all
        assert mock_invoke.call_count == 0
        # Should call large model directly
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1
        assert result == [AIMessage(content="Large model response")]


def test_large_model_callback_parameter(mock_llm_with_large_model):
    """large_model_callback is called when context limit triggers large model."""
    messages = [HumanMessage(content="Test message")]
    callback_called = False

    def test_callback():
        nonlocal callback_called
        callback_called = True

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()
        mock_llm_with_large_model.large_context_model.invoke.return_value = [
            AIMessage(content="Large model response")
        ]

        mock_llm_with_large_model.invoke(messages, large_model_callback=test_callback)

        assert callback_called
        assert mock_invoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1


@patch("time.sleep")
def test_ask_and_repeat_until_with_large_model_stickiness(
    mock_sleep, mock_llm_with_large_model
):
    """Test that ask_and_repeat_until maintains large model stickiness."""
    messages = [HumanMessage(content="Test message")]

    verifier_call_count = 0

    def mock_verifier(response):
        nonlocal verifier_call_count
        verifier_call_count += 1
        if verifier_call_count == 1:
            raise ValueError("Verifier failed on first attempt")
        return "success"

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        # First call triggers context limit error
        mock_invoke.side_effect = MockContextLimitError()

        # Large model succeeds on both calls
        mock_llm_with_large_model.large_context_model.invoke.return_value = [
            AIMessage(content="Large model success")
        ]

        result = mock_llm_with_large_model.ask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default"
        )

        assert result == "success"
        # Regular model should be called once (first attempt triggers context limit)
        assert mock_invoke.call_count == 1
        # Large model should be called twice (first attempt + retry after verifier)
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 2
        assert verifier_call_count == 2


@patch("time.sleep")
def test_ask_and_repeat_until_with_large_model_retry(
    mock_sleep, mock_llm_with_large_model
):
    """Test ask_and_repeat_until with large model retry on server errors."""
    messages = [HumanMessage(content="Test message")]

    def mock_verifier(response):
        return "success"

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()

        mock_llm_with_large_model.large_context_model.invoke.side_effect = [
            MockTimeoutError("Timeout error occurred"),
            [AIMessage(content="Large model success after retry")],
        ]

        result = mock_llm_with_large_model.ask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default"
        )

        assert result == "success"
        assert mock_invoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 2


@pytest.mark.asyncio
@patch("asyncio.sleep")
async def test_aask_and_repeat_until_with_large_model_retry(
    mock_asleep, mock_llm_with_large_model
):
    """Test async ask_and_repeat_until with large model retry."""
    messages = [HumanMessage(content="Test message")]

    def mock_verifier(response):
        return "success"

    with patch.object(mock_llm_with_large_model, "_ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = MockContextLimitError()

        async def timeout_error():
            raise MockTimeoutError("Timeout error occurred")

        async def success_response():
            return [AIMessage(content="Large model success after retry")]

        mock_llm_with_large_model.large_context_model.ainvoke.side_effect = [
            timeout_error(),
            success_response(),
        ]

        result = await mock_llm_with_large_model.aask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default"
        )

        assert result == "success"
        assert mock_ainvoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.ainvoke.call_count == 2


# Tests for LLM without large context model (prepare_large_context_model=False)


def test_force_large_model_when_disabled(mock_llm_without_large_model):
    """Test that force_large_model parameter is ignored when large model is disabled."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_without_large_model, "_invoke") as mock_invoke:
        mock_invoke.return_value = [AIMessage(content="Regular model response")]

        result = mock_llm_without_large_model.invoke(messages, force_large_model=True)

        # Should call regular model even with force_large_model=True
        assert mock_invoke.call_count == 1
        assert result == [AIMessage(content="Regular model response")]


def test_large_model_callback_when_disabled(mock_llm_without_large_model):
    """Test that large_model_callback is never called when large model is disabled."""
    messages = [HumanMessage(content="Test message")]
    callback_called = False

    def test_callback():
        nonlocal callback_called
        callback_called = True

    with patch.object(mock_llm_without_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()

        # Should raise the context limit error instead of calling callback
        with pytest.raises(MockContextLimitError):
            mock_llm_without_large_model.invoke(
                messages, large_model_callback=test_callback
            )

        assert not callback_called
        assert mock_invoke.call_count == 1


def test_context_limit_error_when_large_model_disabled(mock_llm_without_large_model):
    """Test that context limit errors are raised when large model is disabled."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_without_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()

        # Should raise the context limit error instead of falling back
        with pytest.raises(MockContextLimitError):
            mock_llm_without_large_model.invoke(messages)

        assert mock_invoke.call_count == 1


def test_ask_and_repeat_until_context_limit_when_disabled(mock_llm_without_large_model):
    """ask_and_repeat_until behavior when large model is disabled and context limit."""
    messages = [HumanMessage(content="Test message")]

    def mock_verifier(response):
        return "success"

    with patch.object(mock_llm_without_large_model, "_invoke") as mock_invoke:
        mock_invoke.side_effect = MockContextLimitError()

        # Should return default value since context limit error prevents success
        result = mock_llm_without_large_model.ask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default_fallback"
        )

        assert result == "default_fallback"
        # Should try multiple times based on max_retries
        assert mock_invoke.call_count >= 1


@pytest.mark.asyncio
async def test_async_context_limit_error_when_large_model_disabled(
    mock_llm_without_large_model,
):
    """Test that async context limit errors are raised when large model is disabled."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_without_large_model, "_ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = MockContextLimitError()

        # Should raise the context limit error instead of falling back
        with pytest.raises(MockContextLimitError):
            await mock_llm_without_large_model.ainvoke(messages)

        assert mock_ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_aask_and_repeat_until_context_limit_when_disabled(
    mock_llm_without_large_model,
):
    """async ask_and_repeat_until behavior when large model is disabled."""
    messages = [HumanMessage(content="Test message")]

    def mock_verifier(response):
        return "success"

    with patch.object(mock_llm_without_large_model, "_ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = MockContextLimitError()

        # Should return default value since context limit error prevents success
        result = await mock_llm_without_large_model.aask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default_fallback"
        )

        assert result == "default_fallback"
        # Should try multiple times based on max_retries
        assert mock_ainvoke.call_count >= 1


@patch("time.sleep")
def test_large_model_stickiness_on_non_context_errors(
    mock_sleep, mock_llm_with_large_model
):
    """
    CORE BUG TEST: Test that large model remains sticky even when encountering
    non-context-limit errors during ask_and_repeat_until.

    This tests the original issue: when ask_and_repeat_until() triggers large model
    due to context limit, and then the large model encounters a server error,
    the system should continue using the large model for subsequent retries.
    """
    messages = [HumanMessage(content="Test message")]

    verifier_call_count = 0

    def mock_verifier(response):
        nonlocal verifier_call_count
        verifier_call_count += 1
        if verifier_call_count == 1:  # Fail first attempt only
            raise ValueError(f"Verifier failed on attempt {verifier_call_count}")
        return "success"

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        # First call triggers context limit error, switching to large model
        mock_invoke.side_effect = MockContextLimitError()

        # Large model succeeds on both calls
        mock_llm_with_large_model.large_context_model.invoke.return_value = [
            AIMessage(content="Large model response")
        ]

        result = mock_llm_with_large_model.ask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default"
        )

        assert result == "success"
        # Regular model should only be called once (triggers context limit)
        assert mock_invoke.call_count == 1
        # Large model should be called twice (first attempt + retry after verifier)
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 2
        # Verifier should be called twice (1 failure + 1 success)
        assert verifier_call_count == 2


@pytest.mark.asyncio
@patch("time.sleep")
async def test_async_large_model_stickiness_on_non_context_errors(
    mock_sleep,
    mock_llm_with_large_model,
):
    """
    CORE BUG TEST (Async): Test that large model remains sticky even when encountering
    non-context-limit errors during aask_and_repeat_until.
    """
    messages = [HumanMessage(content="Test message")]

    verifier_call_count = 0

    def mock_verifier(response):
        nonlocal verifier_call_count
        verifier_call_count += 1
        if verifier_call_count == 1:  # Fail first attempt only
            raise ValueError(f"Verifier failed on attempt {verifier_call_count}")
        return "success"

    with patch.object(mock_llm_with_large_model, "_ainvoke") as mock_ainvoke:
        # First call triggers context limit error, switching to large model
        mock_ainvoke.side_effect = MockContextLimitError()

        mock_llm_with_large_model.large_context_model.ainvoke = AsyncMock(
            return_value=[AIMessage(content="Large model response")]
        )

        result = await mock_llm_with_large_model.aask_and_repeat_until(
            verifier=mock_verifier, messages=messages, default="default"
        )

        assert result == "success"
        # Regular model should only be called once (triggers context limit)
        assert mock_ainvoke.call_count == 1
        # Large model should be called twice (first attempt + retry after verifier)
        assert mock_llm_with_large_model.large_context_model.ainvoke.call_count == 2
        # Verifier should be called twice (1 failure + 1 success)
        assert verifier_call_count == 2


def test_large_model_fallback_chain(mock_llm_with_large_model):
    """Test the complete fallback chain: regular -> large -> large_fallback."""
    messages = [HumanMessage(content="Test message")]

    with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
        # Regular model fails with context limit
        mock_invoke.side_effect = MockContextLimitError()

        # Primary large model fails with context limit
        mock_llm_with_large_model.large_context_model.invoke.side_effect = (
            MockContextLimitError()
        )

        # Fallback large model succeeds
        mock_llm_with_large_model.large_context_model_fallback.invoke.return_value = [
            AIMessage(content="Fallback model success")
        ]

        result = mock_llm_with_large_model.invoke(messages)

        assert mock_invoke.call_count == 1
        assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1
        assert (
            mock_llm_with_large_model.large_context_model_fallback.invoke.call_count
            == 1
        )
        assert result == [AIMessage(content="Fallback model success")]


def test_unresolvable_error_triggers_fallback(mock_llm_with_large_model):
    """Test that unresolvable errors in large model trigger fallback."""
    messages = [HumanMessage(content="Test message")]

    class MockUnresolvableError(Exception):
        def __init__(
            self,
            message="'type': 'insufficient_quota', 'param': None, 'code': 'insufficient_quota'",  # noqa: E501
        ):
            super().__init__(message)

    mock_llm_with_large_model.large_context_model.invoke.side_effect = (
        MockUnresolvableError()
    )
    mock_llm_with_large_model.large_context_model_fallback.invoke.return_value = [
        AIMessage(content="Fallback after quota error")
    ]

    result = mock_llm_with_large_model.invoke_large_model(messages)

    assert mock_llm_with_large_model.large_context_model.invoke.call_count == 1
    assert mock_llm_with_large_model.large_context_model_fallback.invoke.call_count == 1
    assert result == [AIMessage(content="Fallback after quota error")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
