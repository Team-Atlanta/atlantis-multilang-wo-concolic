# flake8: noqa: E501
import os
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.rate_limiters import InMemoryRateLimiter

from mlla.utils.bedrock_callback import BedrockTokenUsageCallbackHandler
from mlla.utils.context import GlobalContext
from mlla.utils.llm import LLM

# Real TOKEN_COSTS data for testing
REAL_TOKEN_COSTS = {
    "claude-sonnet-4-20250514": {
        "max_input_tokens": 200000,
        "max_output_tokens": 64000,
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
    },
    "claude-opus-4-20250514": {
        "max_input_tokens": 200000,
        "max_output_tokens": 32000,
        "input_cost_per_token": 0.000015,
        "output_cost_per_token": 0.000075,
    },
    "o3": {
        "max_input_tokens": 128000,
        "max_output_tokens": 100000,
        "input_cost_per_token": 0.00006,
        "output_cost_per_token": 0.00024,
    },
    "gemini-2.5-pro": {
        "max_input_tokens": 2000000,
        "max_output_tokens": 65535,
        "input_cost_per_token": 0.00000125,
        "output_cost_per_token": 0.000005,
    },
    "gpt-4.1": {
        "max_input_tokens": 128000,
        "max_output_tokens": 32768,
        "input_cost_per_token": 0.00001,
        "output_cost_per_token": 0.00003,
    },
}


class MockRateLimitError(Exception):
    """Mock rate limit error that mimics real rate limit exceptions."""

    def __init__(self, message="RateLimitError: Too many requests"):
        super().__init__(message)


class MockContextLimitError(Exception):
    """Mock server error that mimics real server exceptions."""

    def __init__(self, message="input length and `max_tokens` exceed context limit"):
        super().__init__(message)


def create_real_global_context() -> GlobalContext:
    """Create a real GlobalContext instance with test-appropriate settings."""
    # Set required environment variables for testing
    os.environ.setdefault("OPENAI_TIMEOUT", "30")
    os.environ.setdefault("OPENAI_MAX_RETRIES", "3")
    os.environ.setdefault("GEMINI_TIMEOUT", "30")
    os.environ.setdefault("GEMINI_MAX_RETRIES", "3")
    os.environ.setdefault("ATLANTA_TIMEOUT", "30")
    os.environ.setdefault("ATLANTA_MAX_RETRIES", "3")
    os.environ.setdefault("MAX_CONCURRENT_ASYNC_LLM_CALLS", "10")

    # Create a minimal GlobalContext for testing
    # We'll mock the complex initialization parts
    config = GlobalContext.__new__(GlobalContext)

    # Set basic attributes
    config.api_key = os.getenv("LITELLM_KEY", "asdf")
    config.base_url = os.getenv("LITELLM_URL", "http://example.com")
    config.is_dev = False
    config.openai_timeout = 30
    config.openai_max_retries = 3
    config.gemini_timeout = 30
    config.gemini_max_retries = 3
    config.atlanta_timeout = 30
    config.atlanta_max_retries = 3
    config.max_concurrent_async_llm_calls = 10

    # Create real callback and rate limiter instances
    config.general_callback = BedrockTokenUsageCallbackHandler()
    config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    return config


@pytest.fixture
def real_config():
    """Create a real GlobalContext for testing."""
    return create_real_global_context()


class TestClaudeComplexFallbackReal:
    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    @patch("time.sleep")
    def test_complex_scenario_1_rate_limit_to_context_limit_to_large_model(
        self, mock_sleep, real_config
    ):
        """
        Test Scenario 1: claude-sonnet-4 -> rate limit error (max reaches) -> opus-4 -> context limit error -> large_context model
        """
        # Setup
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=True,
        )
        execution_flow = []

        # Store original methods
        original_create_fallback = llm._create_fallback_llm

        # Mock functions
        def mock_sonnet_rate_limit(*args, **kwargs):
            execution_flow.append("sonnet_rate_limit")
            raise MockRateLimitError()

        def mock_opus_context_limit(*args, **kwargs):
            execution_flow.append("opus_context_limit")
            raise MockContextLimitError()

        def mock_large_model_success(*args, **kwargs):
            execution_flow.append("large_model_success")
            return [AIMessage(content="Large model response")]

        def patched_create_fallback(model_name, prepare_large_context_model=True):
            """Create fallback LLM and mock its invoke method if it's opus"""
            execution_flow.append(f"create_fallback_{model_name}")
            fallback_llm = original_create_fallback(
                model_name, prepare_large_context_model
            )

            # If this is the opus fallback, mock its _invoke_model_with_retry to fail with context limit
            # and mock its invoke_large_model to succeed
            if "opus" in model_name:
                fallback_llm._invoke_model_with_retry = mock_opus_context_limit
                fallback_llm.invoke_large_model = mock_large_model_success

            return fallback_llm

        # Execute test with mocked chain
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_sonnet_rate_limit
        ):
            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ):
                messages = [HumanMessage(content="Test message")]
                result = llm.invoke(messages)

        # Verify execution flow
        expected_steps = [
            "sonnet_rate_limit",
            "create_fallback_claude-opus-4-20250514",
            "opus_context_limit",
            "large_model_success",
        ]

        for step in expected_steps:
            assert (
                step in execution_flow
            ), f"Missing execution step: {step}. Actual flow: {execution_flow}"

        # Verify result
        assert len(result) >= 1
        assert isinstance(result[-1], AIMessage)
        assert result[-1].content == "Large model response"

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    @patch("time.sleep")
    def test_complex_scenario_2_opus_to_sonnet_fallback(self, mock_sleep, real_config):
        """
        Test Scenario 2: claude-opus-4 -> rate limit error -> sonnet-4 -> context limit error -> large_context model
        """
        # Setup
        llm = LLM(
            model="claude-opus-4-20250514",
            config=real_config,
            prepare_large_context_model=True,
        )
        execution_flow = []

        # Store original methods
        original_create_fallback = llm._create_fallback_llm

        # Mock functions
        def mock_opus_rate_limit(*args, **kwargs):
            execution_flow.append("opus_rate_limit")
            raise MockRateLimitError()

        def mock_sonnet_context_limit(*args, **kwargs):
            execution_flow.append("sonnet_context_limit")
            raise MockContextLimitError()

        def mock_large_model_success(*args, **kwargs):
            execution_flow.append("large_model_success")
            return [AIMessage(content="Large model response")]

        def patched_create_fallback(model_name, prepare_large_context_model=True):
            """Create fallback LLM and mock its invoke method if it's sonnet"""
            execution_flow.append(f"create_fallback_{model_name}")
            fallback_llm = original_create_fallback(
                model_name, prepare_large_context_model
            )

            # If this is the sonnet fallback, mock its _invoke_model_with_retry to fail with context limit
            # and mock its invoke_large_model to succeed
            if "sonnet" in model_name:
                fallback_llm._invoke_model_with_retry = mock_sonnet_context_limit
                fallback_llm.invoke_large_model = mock_large_model_success

            return fallback_llm

        # Execute test with mocked chain
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_opus_rate_limit
        ):
            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ):
                messages = [HumanMessage(content="Test message")]
                result = llm.invoke(messages)

        # Verify execution flow
        expected_steps = [
            "opus_rate_limit",
            "create_fallback_claude-sonnet-4-20250514",
            "sonnet_context_limit",
            "large_model_success",
        ]

        for step in expected_steps:
            assert (
                step in execution_flow
            ), f"Missing execution step: {step}. Actual flow: {execution_flow}"

        # Verify result
        assert len(result) >= 1
        assert isinstance(result[-1], AIMessage)
        assert result[-1].content == "Large model response"

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    @patch("time.sleep")
    def test_complex_scenario_3_large_input_with_summarization(
        self, mock_sleep, real_config
    ):
        """
        Test Scenario 3: claude-sonnet-4 -> rate limit -> opus-4 -> context limit -> large_context model -> summarization needed

        This test combines both content-based and token-based summarization triggers:
        - Large input content naturally leads to high token counts
        - Token limits are exceeded, forcing summarization
        - Summarization is performed and large model succeeds
        """
        # Setup
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=True,
        )
        execution_flow = []

        # Store original methods
        original_create_fallback = llm._create_fallback_llm

        # Mock functions
        def mock_sonnet_rate_limit(*args, **kwargs):
            execution_flow.append("sonnet_rate_limit")
            raise MockRateLimitError()

        def mock_opus_context_limit(*args, **kwargs):
            execution_flow.append("opus_context_limit")
            raise MockContextLimitError()

        def mock_tokenize_realistic(self, messages):
            """Mock tokenize to return realistic high token counts for large messages"""
            execution_flow.append("tokenize_called")
            # Return high token counts based on actual message length
            token_counts = []
            for msg in messages:
                # Simulate realistic token counting: ~4 chars per token for large content
                estimated_tokens = max(
                    len(msg.content) // 4, 1000
                )  # At least 1k tokens per message
                token_counts.append((estimated_tokens, msg))
            return token_counts

        def mock_get_context_limit(self):
            """Mock context limit that will be exceeded by our large messages"""
            return 8000  # 8k token limit - will be exceeded by our large messages

        def mock_large_context_model_invoke(*args, **kwargs):
            """Mock the large context model's _invoke_model_with_retry to succeed after summarization"""
            execution_flow.append("large_model_success")
            return AIMessage(content="Large model response after summarization")

        def patched_create_fallback(model_name, prepare_large_context_model=True):
            """Create fallback LLM and mock its methods if it's opus"""
            execution_flow.append(f"create_fallback_{model_name}")
            fallback_llm = original_create_fallback(
                model_name, prepare_large_context_model
            )

            # If this is the opus fallback, mock its methods
            if "opus" in model_name:
                fallback_llm._invoke_model_with_retry = mock_opus_context_limit

                # Mock the large context model's methods to trigger realistic summarization
                # Don't mock invoke_large_model - let it call the large context model naturally
                if fallback_llm.large_context_model:
                    # Store original summarize method
                    original_summarize = fallback_llm.large_context_model.summarize

                    def tracked_summarize(messages):
                        execution_flow.append("summarization_performed")
                        return original_summarize(messages)

                    # Mock the large context model's methods to trigger summarization flow
                    fallback_llm.large_context_model.tokenize = (
                        lambda msgs: mock_tokenize_realistic(
                            fallback_llm.large_context_model, msgs
                        )
                    )
                    fallback_llm.large_context_model.get_context_limit = (
                        lambda: mock_get_context_limit(fallback_llm.large_context_model)
                    )
                    fallback_llm.large_context_model.summarize = tracked_summarize
                    # Mock the actual model invocation to succeed after summarization
                    fallback_llm.large_context_model._invoke_model_with_retry = (
                        mock_large_context_model_invoke
                    )

            return fallback_llm

        # Create large input messages that would realistically trigger both content and token limits
        large_messages = [
            HumanMessage(
                content="System: You are a helpful assistant analyzing large datasets."
            ),
            HumanMessage(
                content="Please analyze this large data: " + "A" * 2000
            ),  # ~2k chars
            AIMessage(content="Analysis result: " + "B" * 2000),  # ~2k chars
            HumanMessage(
                content="Now process this additional data: " + "C" * 2000
            ),  # ~2k chars
            AIMessage(content="Processing complete: " + "D" * 2000),  # ~2k chars
            HumanMessage(content="Final question: What are the key insights?"),
        ]

        # Execute test with mocked chain
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_sonnet_rate_limit
        ):
            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ):
                result = llm.invoke(large_messages)

        # Verify execution flow includes all expected steps
        expected_steps = [
            "sonnet_rate_limit",
            "create_fallback_claude-opus-4-20250514",
            "opus_context_limit",
            "tokenize_called",
            "summarization_performed",
            "large_model_success",
        ]

        for step in expected_steps:
            assert (
                step in execution_flow
            ), f"Missing execution step: {step}. Actual flow: {execution_flow}"

        # Verify result
        assert len(result) >= 1
        assert isinstance(result[-1], AIMessage)
        assert result[-1].content == "Large model response after summarization"


if __name__ == "__main__":
    pytest.main([__file__])
