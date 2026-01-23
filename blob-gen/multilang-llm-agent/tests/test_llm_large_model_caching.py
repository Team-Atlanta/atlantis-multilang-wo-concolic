"""
Test cases for large model caching behavior in LLM class.
Tests that cache control is properly stripped from messages when using large models
to prevent unintended caching behavior.
"""

from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.rate_limiters import InMemoryRateLimiter

from mlla.utils.context import GlobalContext
from mlla.utils.llm import LLM
from mlla.utils.messages import add_cache_control, remove_cache_control


class MockContextLimitError(Exception):
    def __init__(self, message="input length and `max_tokens` exceed context limit"):
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
    config.global_rate_limiter = InMemoryRateLimiter(requests_per_second=10)
    config.global_claude_rate_limiter = InMemoryRateLimiter(requests_per_second=5)
    return config


@pytest.fixture
def mock_llm_with_large_model(mock_config):
    """LLM with large context model enabled."""
    with patch("mlla.utils.llm.ChatOpenAI"), patch(
        "mlla.utils.llm.ChatAnthropic"
    ), patch(
        "mlla.utils.llm.TOKEN_COSTS",
        {
            "claude-3-5-sonnet-20241022": {
                "max_input_tokens": 200000,
                "max_output_tokens": 8192,
            },
            "gpt-4.1": {"max_input_tokens": 128000, "max_output_tokens": 4096},
            "gemini-2.5-pro": {
                "max_input_tokens": 1000000,
                "max_output_tokens": 8192,
            },
        },
    ):
        llm = LLM(
            model="claude-3-5-sonnet-20241022",
            config=mock_config,
            prepare_large_context_model=True,
        )

        # Mock the large context model
        llm.large_context_model = Mock()
        llm.large_context_model.invoke = Mock()
        llm.large_context_model.model_name = "gemini-2.5-pro"

        # Mock the large context model fallback
        llm.large_context_model_fallback = Mock()
        llm.large_context_model_fallback.invoke = Mock()
        llm.large_context_model_fallback.model_name = "gpt-4.1"

        return llm


def test_remove_cache_control_function():
    """Test the remove_cache_control function directly."""
    # Test with cache-controlled content
    message = HumanMessage(content="Test message")
    add_cache_control(message)

    # Verify cache control was added
    assert isinstance(message.content, list)
    assert "cache_control" in message.content[0]

    # Remove cache control
    result = remove_cache_control(message)
    assert result.content == "Test message"


def test_context_limit_fallback_strips_cache_control(mock_llm_with_large_model):
    """
    Core test scenario:
    1) General invoke with caching enabled
    2) Meets context limit error
    3) Fallback to large model
    4) Check all messages do not have cache_control
    """
    # Step 1: Create messages w/ caching (simulating normal invoke, cache=True)
    messages = [HumanMessage(content="Test message")]

    # Mock the _prepare_messages to simulate cache control during normal processing
    original_prepare_messages = mock_llm_with_large_model._prepare_messages

    def mock_prepare_messages(msgs, choice, cache=None):
        # Simulate what happens in real _prepare_messages when cache=True
        if cache:
            for msg in msgs:
                add_cache_control(msg)
        return original_prepare_messages(msgs, choice, cache)

    with patch.object(
        mock_llm_with_large_model,
        "_prepare_messages",
        side_effect=mock_prepare_messages,
    ):
        # Step 2: Mock regular model to throw context limit error
        with patch.object(mock_llm_with_large_model, "_invoke") as mock_invoke:
            mock_invoke.side_effect = MockContextLimitError()

            # Step 3: Mock large model response
            mock_llm_with_large_model.large_context_model.invoke.return_value = [
                AIMessage(content="Large model success")
            ]

            # Call invoke with cache=True (should trigger fallback to large model)
            mock_llm_with_large_model.invoke(messages, cache=True)

            # Verify the flow happened as expected
            assert mock_invoke.call_count == 1  # Regular model was called once
            assert (
                mock_llm_with_large_model.large_context_model.invoke.call_count == 1
            )  # Large model was called

            # Step 4: Get the messages passed to large model and verify NO cache control
            call_args = mock_llm_with_large_model.large_context_model.invoke.call_args
            passed_messages = call_args[1]["messages"]

            # Check ALL messages do not have cache_control
            for msg in passed_messages:
                if isinstance(msg.content, str):
                    # Simple string content - no cache control possible
                    assert isinstance(msg.content, str)
                elif isinstance(msg.content, list):
                    # List content - check each item for cache_control
                    for item in msg.content:
                        if isinstance(item, dict):
                            assert (
                                "cache_control" not in item
                            ), f"Found cache_control in message: {item}"
                else:
                    # Other content types should not have cache control
                    pass

            # Verify cache=False was explicitly passed to large model
            assert call_args[1]["cache"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
