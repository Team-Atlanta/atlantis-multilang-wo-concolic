import os
from unittest.mock import patch

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_openai import ChatOpenAI

from mlla.utils.bedrock_callback import BedrockTokenUsageCallbackHandler
from mlla.utils.context import GlobalContext
from mlla.utils.llm import LLM, get_rate_limit_fallback_model_name

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


class MockServerError(Exception):
    """Mock server error that mimics real server exceptions."""

    def __init__(
        self, message="ServiceUnavailableError: Server temporarily unavailable"
    ):
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


class TestRateLimitFallbackReal:
    """Test rate limit fallback functionality using real data structures."""

    def test_get_rate_limit_fallback_model_name(self):
        """Test the fallback model name mapping."""
        # Test Claude Sonnet fallback to Opus
        assert (
            get_rate_limit_fallback_model_name("claude-sonnet-4-20250514")
            == "claude-opus-4-20250514"
        )

        # Test Claude Opus fallback to Sonnet
        assert (
            get_rate_limit_fallback_model_name("claude-opus-4-20250514")
            == "claude-sonnet-4-20250514"
        )

        # Test all other models fallback to o3
        assert get_rate_limit_fallback_model_name("o3") == "o3"
        assert get_rate_limit_fallback_model_name("gemini-2.5-pro") == "o3"
        assert get_rate_limit_fallback_model_name("gpt-4.1") == "o3"
        assert get_rate_limit_fallback_model_name("unknown-model") == "o3"
        assert get_rate_limit_fallback_model_name("gpt-4o") == "o3"
        assert get_rate_limit_fallback_model_name("claude-haiku") == "o3"

    def test_retry_count_logic_opus_vs_others(self):
        """Test that opus models get 1 retry while others get 5."""
        # Test opus model retry count
        opus_config = create_real_global_context()
        opus_llm = LLM(
            model="claude-opus-4-20250514",
            config=opus_config,
            prepare_large_context_model=False,
        )

        # Test sonnet model retry count
        sonnet_config = create_real_global_context()
        sonnet_llm = LLM(
            model="claude-sonnet-4-20250514",
            config=sonnet_config,
            prepare_large_context_model=False,
        )

        # Test o3 model retry count
        o3_config = create_real_global_context()
        o3_llm = LLM(
            model="o3",
            config=o3_config,
            prepare_large_context_model=False,
        )

        # Mock the _invoke_model_with_retry to capture max_retries and second_chance
        retry_params = {}

        def capture_opus_retries(*args, max_retries=5, second_chance=False, **kwargs):
            retry_params["opus"] = {
                "max_retries": max_retries,
                "second_chance": second_chance,
            }
            raise MockRateLimitError("Rate limit for testing")

        def capture_sonnet_retries(*args, max_retries=5, second_chance=False, **kwargs):
            retry_params["sonnet"] = {
                "max_retries": max_retries,
                "second_chance": second_chance,
            }
            raise MockRateLimitError("Rate limit for testing")

        def capture_o3_retries(*args, max_retries=5, second_chance=False, **kwargs):
            retry_params["o3"] = {
                "max_retries": max_retries,
                "second_chance": second_chance,
            }
            raise MockRateLimitError("Rate limit for testing")

        messages = [HumanMessage(content="Test message")]

        # Test opus retry count (should be 1, no second chance)
        with patch.object(
            opus_llm, "_invoke_model_with_retry", side_effect=capture_opus_retries
        ):
            with patch.object(opus_llm, "_create_fallback_llm") as mock_fallback:
                mock_fallback.return_value._invoke_model_with_retry = (
                    lambda *args, **kwargs: AIMessage(content="fallback")
                )
                mock_fallback.return_value.invoke = lambda *args, **kwargs: [
                    AIMessage(content="fallback")
                ]
                try:
                    opus_llm.invoke(messages)
                except Exception:
                    pass

        # Test sonnet retry count (should be 5, with second chance)
        with patch.object(
            sonnet_llm, "_invoke_model_with_retry", side_effect=capture_sonnet_retries
        ):
            with patch.object(sonnet_llm, "_create_fallback_llm") as mock_fallback:
                mock_fallback.return_value._invoke_model_with_retry = (
                    lambda *args, **kwargs: AIMessage(content="fallback")
                )
                mock_fallback.return_value.invoke = lambda *args, **kwargs: [
                    AIMessage(content="fallback")
                ]
                try:
                    sonnet_llm.invoke(messages)
                except Exception:
                    pass

        # Test o3 retry count (should be 5, no second chance)
        with patch.object(
            o3_llm, "_invoke_model_with_retry", side_effect=capture_o3_retries
        ):
            with patch.object(o3_llm, "_create_fallback_llm") as mock_fallback:
                mock_fallback.return_value._invoke_model_with_retry = (
                    lambda *args, **kwargs: AIMessage(content="fallback")
                )
                mock_fallback.return_value.invoke = lambda *args, **kwargs: [
                    AIMessage(content="fallback")
                ]
                try:
                    o3_llm.invoke(messages)
                except Exception:
                    pass

        # Verify retry counts and second chance settings
        assert retry_params["opus"]["max_retries"] == 1, (
            "Opus should have 1 retry, got"
            f" {retry_params.get('opus', {}).get('max_retries')}"
        )
        assert not retry_params["opus"]["second_chance"], (
            "Opus should not have second chance, got"
            f" {retry_params.get('opus', {}).get('second_chance')}"
        )

        assert retry_params["sonnet"]["max_retries"] == 5, (
            "Sonnet should have 5 retries, got"
            f" {retry_params.get('sonnet', {}).get('max_retries')}"
        )
        assert retry_params["sonnet"]["second_chance"], (
            "Sonnet should have second chance, got"
            f" {retry_params.get('sonnet', {}).get('second_chance')}"
        )

        assert retry_params["o3"]["max_retries"] == 5, (
            "O3 should have 5 retries, got"
            f" {retry_params.get('o3', {}).get('max_retries')}"
        )
        assert not retry_params["o3"]["second_chance"], (
            "O3 should not have second chance, got"
            f" {retry_params.get('o3', {}).get('second_chance')}"
        )

    @pytest.mark.asyncio
    async def test_async_retry_count_logic_opus_vs_others(self):
        """Test that opus models get 1 retry while others get 5 in async calls."""
        # Test opus model retry count
        opus_config = create_real_global_context()
        opus_llm = LLM(
            model="claude-opus-4-20250514",
            config=opus_config,
            prepare_large_context_model=False,
        )

        # Test sonnet model retry count
        sonnet_config = create_real_global_context()
        sonnet_llm = LLM(
            model="claude-sonnet-4-20250514",
            config=sonnet_config,
            prepare_large_context_model=False,
        )

        # Mock the _ainvoke_model_with_retry to capture max_retries and second_chance
        retry_params = {}

        async def capture_opus_retries(
            *args, max_retries=5, second_chance=False, **kwargs
        ):
            retry_params["opus"] = {
                "max_retries": max_retries,
                "second_chance": second_chance,
            }
            raise MockRateLimitError("Rate limit for testing")

        async def capture_sonnet_retries(
            *args, max_retries=5, second_chance=False, **kwargs
        ):
            retry_params["sonnet"] = {
                "max_retries": max_retries,
                "second_chance": second_chance,
            }
            raise MockRateLimitError("Rate limit for testing")

        messages = [HumanMessage(content="Test message")]

        # Test opus retry count (should be 1, no second chance)
        with patch.object(
            opus_llm, "_ainvoke_model_with_retry", side_effect=capture_opus_retries
        ):
            with patch.object(opus_llm, "_create_fallback_llm") as mock_fallback:
                mock_fallback.return_value._ainvoke_model_with_retry = (
                    lambda *args, **kwargs: AIMessage(content="fallback")
                )
                mock_fallback.return_value.ainvoke = lambda *args, **kwargs: [
                    AIMessage(content="fallback")
                ]
                try:
                    await opus_llm.ainvoke(messages)
                except Exception:
                    pass

        # Test sonnet retry count (should be 5, with second chance)
        with patch.object(
            sonnet_llm, "_ainvoke_model_with_retry", side_effect=capture_sonnet_retries
        ):
            with patch.object(sonnet_llm, "_create_fallback_llm") as mock_fallback:
                mock_fallback.return_value._ainvoke_model_with_retry = (
                    lambda *args, **kwargs: AIMessage(content="fallback")
                )
                mock_fallback.return_value.ainvoke = lambda *args, **kwargs: [
                    AIMessage(content="fallback")
                ]
                try:
                    await sonnet_llm.ainvoke(messages)
                except Exception:
                    pass

        # Verify retry counts and second chance settings
        assert retry_params["opus"]["max_retries"] == 1, (
            "Opus should have 1 retry, got"
            f" {retry_params.get('opus', {}).get('max_retries')}"
        )
        assert not retry_params["opus"]["second_chance"], (
            "Opus should not have second chance, got"
            f" {retry_params.get('opus', {}).get('second_chance')}"
        )

        assert retry_params["sonnet"]["max_retries"] == 5, (
            "Sonnet should have 5 retries, got"
            f" {retry_params.get('sonnet', {}).get('max_retries')}"
        )
        assert retry_params["sonnet"]["second_chance"], (
            "Sonnet should have second chance, got"
            f" {retry_params.get('sonnet', {}).get('second_chance')}"
        )

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_create_real_llm_instance(self, real_config):
        """Test creating a real LLM instance with actual data structures."""
        # Create primary LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            tools=[],
            temperature=0.5,
            agent_name="test_agent",
            prepare_large_context_model=False,
        )

        # Verify real LLM properties
        assert llm.model_name == "claude-sonnet-4-20250514"
        assert llm.agent_name == "test_agent"
        assert isinstance(llm.chat_model, ChatAnthropic)
        assert isinstance(llm.runnable_chat_model, ChatAnthropic)
        assert llm.runnable_chat_model.temperature == 0.5
        assert llm.runnable_chat_model.max_tokens == 64000  # From REAL_TOKEN_COSTS

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_create_fallback_llm_real_structures(self, real_config):
        """Test fallback LLM creation with real data structures."""
        # Create primary LLM
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            tools=[],
            temperature=0.5,
            agent_name="test_agent",
            prepare_large_context_model=False,
        )

        # Create fallback LLM using real method
        fallback_llm = llm._create_fallback_llm("claude-opus-4-20250514")

        # Verify fallback LLM has real structures
        assert fallback_llm.model_name == "claude-opus-4-20250514"
        assert fallback_llm.agent_name == "test_agent"
        assert isinstance(fallback_llm.chat_model, ChatAnthropic)
        assert isinstance(fallback_llm.runnable_chat_model, ChatAnthropic)
        assert fallback_llm.runnable_chat_model.temperature == 0.5
        assert (
            fallback_llm.runnable_chat_model.max_tokens == 32000
        )  # From REAL_TOKEN_COSTS

        # Verify they share the same config but are different instances
        assert fallback_llm.gc is llm.gc
        assert fallback_llm is not llm
        assert fallback_llm.runnable_chat_model is not llm.runnable_chat_model

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_o_model_temperature_handling_real(self, real_config):
        """Test that o* models get temperature=1.0 with real structures."""
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            temperature=0.5,
            prepare_large_context_model=False,
        )

        # Create fallback for o3 model
        fallback_llm = llm._create_fallback_llm("o3")

        # Verify o3 model gets temperature=1.0 in real ChatOpenAI instance
        assert fallback_llm.model_name == "o3"
        assert isinstance(fallback_llm.chat_model, ChatOpenAI)
        assert fallback_llm.runnable_chat_model.temperature == 1.0

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_rate_limit_fallback_with_real_structures(self, real_config):
        """Test rate limit fallback using real LLM structures but mocked HTTP calls."""
        # Create primary LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Mock the _invoke_model_with_retry method to simulate rate limit error
        def mock_invoke_with_rate_limit(*args, **kwargs):
            raise MockRateLimitError("RateLimitError: Too many requests")

        # Patch the LLM's internal invoke method
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_invoke_with_rate_limit
        ):
            # Mock the fallback's _invoke_model_with_retry method to return success
            def mock_fallback_success(*args, **kwargs):
                return AIMessage(content="Fallback response from real structure")

            # Test messages with real structure
            messages = [HumanMessage(content="Test message")]

            original_create_fallback = llm._create_fallback_llm

            def patched_create_fallback(model_name, prepare_large_context_model=False):
                fallback_llm = original_create_fallback(
                    model_name, prepare_large_context_model
                )
                # Mock the fallback's _invoke_model_with_retry method
                fallback_llm._invoke_model_with_retry = mock_fallback_success
                return fallback_llm

            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ) as mock_create:
                # Call invoke - should trigger fallback with real structures
                result = llm.invoke(messages)

                # Verify fallback was created with real model name
                mock_create.assert_called_once_with(
                    "claude-opus-4-20250514", prepare_large_context_model=True
                )

                # Verify we got the expected response
                assert len(result) >= 1  # Should have at least the response
                assert isinstance(result[-1], AIMessage)
                assert result[-1].content == "Fallback response from real structure"

    @pytest.mark.asyncio
    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    async def test_async_rate_limit_fallback_real_structures(self, real_config):
        """Test async rate limit fallback using real structures."""
        # Create primary LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Mock only the HTTP layer to simulate rate limit error
        async def mock_ainvoke_with_rate_limit(*args, **kwargs):
            raise MockRateLimitError("RateLimitError: Too many requests")

        # Mock the fallback's HTTP layer to return success
        async def mock_fallback_success(*args, **kwargs):
            return AIMessage(content="Async fallback response from real structure")

        # Patch the actual model's ainvoke method (HTTP layer)
        with patch.object(
            llm, "_ainvoke_model_with_retry", side_effect=mock_ainvoke_with_rate_limit
        ):
            # Test messages with real structure
            messages = [HumanMessage(content="Test message")]

            # Patch the fallback creation to use real structures but mock HTTP
            original_create_fallback = llm._create_fallback_llm

            def patched_create_fallback(model_name, prepare_large_context_model=False):
                fallback_llm = original_create_fallback(
                    model_name, prepare_large_context_model
                )
                # Mock the fallback's _ainvoke_model_with_retry method
                fallback_llm._ainvoke_model_with_retry = mock_fallback_success
                return fallback_llm

            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ) as mock_create:
                # Call ainvoke - should trigger fallback with real structures
                result = await llm.ainvoke(messages)

                # Verify fallback was created with real model name
                mock_create.assert_called_once_with(
                    "claude-opus-4-20250514", prepare_large_context_model=True
                )

                # Verify we got the expected response
                assert len(result) == 2  # Original message + response
                assert isinstance(result[-1], AIMessage)
                assert (
                    result[-1].content == "Async fallback response from real structure"
                )

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_chained_fallback_real_structures(self, real_config):
        """Test chained fallback when multiple models fail using real structures."""
        # Create primary LLM
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Mock HTTP layer to simulate rate limit error
        def mock_invoke_with_rate_limit(*args, **kwargs):
            raise MockRateLimitError("RateLimitError: Too many requests")

        # Mock successful response for final fallback
        def mock_final_success(*args, **kwargs):
            return AIMessage(content="Final fallback response from real structure")

        # Patch the primary model's HTTP layer
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_invoke_with_rate_limit
        ):
            # Track fallback creation calls
            fallback_calls = []
            original_create_fallback = llm._create_fallback_llm

            def patched_create_fallback(model_name, prepare_large_context_model=False):
                fallback_calls.append(model_name)
                fallback_llm = original_create_fallback(
                    model_name, prepare_large_context_model
                )

                fallback_llm._invoke_model_with_retry = mock_invoke_with_rate_limit

                # Also patch the fallback's _create_fallback_llm to track nested calls
                original_fallback_create = fallback_llm._create_fallback_llm

                def nested_create_fallback(
                    nested_model_name, prepare_large_context_model=False
                ):
                    fallback_calls.append(f"nested_{nested_model_name}")
                    nested_llm = original_fallback_create(
                        nested_model_name, prepare_large_context_model
                    )
                    # Make the final nested fallback succeed
                    nested_llm._invoke_model_with_retry = mock_final_success
                    return nested_llm

                fallback_llm._create_fallback_llm = nested_create_fallback
                return fallback_llm

            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ):
                # Test messages
                messages = [HumanMessage(content="Test message")]

                # Call invoke - should trigger chained fallback
                result = llm.invoke(messages)

                # Verify first fallback was attempted
                assert "claude-opus-4-20250514" in fallback_calls
                # Verify nested fallback was also created
                assert "nested_claude-sonnet-4-20250514" in fallback_calls

                # Verify final result comes from the nested fallback
                assert len(result) >= 1
                assert isinstance(result[-1], AIMessage)
                assert (
                    result[-1].content == "Final fallback response from real structure"
                )

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_non_rate_limit_error_no_fallback_real(self, real_config):
        """Test that non-rate-limit errors don't trigger fallback."""
        # Create primary LLM
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Mock HTTP layer to simulate non-rate-limit server error
        def mock_invoke_with_server_error(*args, **kwargs):
            raise MockServerError("ServiceUnavailableError: Server down")

        # Patch the actual model's invoke method
        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_invoke_with_server_error
        ):
            # Track if fallback creation is attempted
            with patch.object(llm, "_create_fallback_llm") as mock_create_fallback:
                # Test messages
                messages = [HumanMessage(content="Test message")]

                # Call invoke - should raise the original error, not trigger fallback
                with pytest.raises(MockServerError):
                    llm.invoke(messages)

                # Verify fallback was NOT created
                mock_create_fallback.assert_not_called()

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_context_limit_handling_real_structures(self, real_config):
        """Test context limit handling with real structures."""
        # Create LLM with large context model
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=True,
        )

        # Verify large context model was created with real structures
        assert llm.large_context_model is not None
        assert llm.large_context_model.model_name == "gemini-2.5-pro"
        assert isinstance(llm.large_context_model.chat_model, ChatOpenAI)

        # Verify fallback large context model
        if llm.large_context_model_fallback:
            assert llm.large_context_model_fallback.model_name == "gpt-4.1"

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_real_message_processing(self, real_config):
        """Test that real message processing works correctly."""
        # Create LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Create real message objects
        messages = [
            HumanMessage(content="Test message", id="msg-1"),
            AIMessage(content="Test response", id="msg-2"),
        ]

        # Test message preparation (this uses real logic)
        prepared_messages, remove_messages = llm._prepare_messages(
            messages, choice="NOCHANGE"
        )

        # Verify real message processing
        assert len(prepared_messages) >= len(messages)  # May add system messages
        assert all(hasattr(msg, "content") for msg in prepared_messages)
        assert all(
            hasattr(msg, "id") for msg in prepared_messages if hasattr(msg, "id")
        )

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_real_tokenization(self, real_config):
        """Test real tokenization with actual message structures."""
        # Create LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Create real message objects
        messages = [
            HumanMessage(content="This is a test message for tokenization"),
            AIMessage(content="This is a response message"),
        ]

        # Test real tokenization
        token_counts = llm.tokenize(messages)

        # Verify tokenization results
        assert len(token_counts) == len(messages)
        assert all(isinstance(count, int) and count > 0 for count, _ in token_counts)
        assert all(
            msg == original for (_, msg), original in zip(token_counts, messages)
        )

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_real_context_limits(self, real_config):
        """Test real context limit calculations."""
        # Test different model types
        models_to_test = [
            ("claude-sonnet-4-20250514", ChatAnthropic),
            ("o3", ChatOpenAI),
            ("gemini-2.5-pro", ChatOpenAI),
        ]

        for model_name, expected_type in models_to_test:
            llm = LLM(
                model=model_name, config=real_config, prepare_large_context_model=False
            )

            # Verify real context limit calculation
            context_limit = llm.get_context_limit()
            output_limit = llm.get_output_limit()

            # Verify limits are reasonable
            assert context_limit > 0
            assert output_limit > 0
            assert isinstance(llm.chat_model, expected_type)

            # Verify limits match TOKEN_COSTS
            expected_input = REAL_TOKEN_COSTS[model_name]["max_input_tokens"]
            expected_output = REAL_TOKEN_COSTS[model_name]["max_output_tokens"]

            # Context limit should be less than max input
            assert context_limit < expected_input
            assert output_limit == expected_output

    @patch("mlla.utils.llm.TOKEN_COSTS", REAL_TOKEN_COSTS)
    def test_parameter_preservation_in_fallback_real(self, real_config):
        """Test that fallback preserves all parameters with real structures."""
        # Create LLM with real structures
        llm = LLM(
            model="claude-sonnet-4-20250514",
            config=real_config,
            prepare_large_context_model=False,
        )

        # Mock HTTP layer to simulate rate limit error
        def mock_invoke_with_rate_limit(*args, **kwargs):
            raise MockRateLimitError("RateLimitError: Too many requests")

        # Mock successful fallback
        def mock_fallback_success(*args, **kwargs):
            return AIMessage(content="Fallback success")

        with patch.object(
            llm, "_invoke_model_with_retry", side_effect=mock_invoke_with_rate_limit
        ):
            # Track parameters passed to fallback
            fallback_params = {}
            original_create_fallback = llm._create_fallback_llm

            def patched_create_fallback(model_name, prepare_large_context_model=False):
                fallback_llm = original_create_fallback(
                    model_name, prepare_large_context_model
                )

                def capture_invoke(*args, **kwargs):
                    fallback_params.update(kwargs)
                    return [AIMessage(content="Fallback response")]

                fallback_llm.invoke = capture_invoke
                return fallback_llm

            with patch.object(
                llm, "_create_fallback_llm", side_effect=patched_create_fallback
            ):
                # Test with various parameters
                messages = [HumanMessage(content="Test message")]
                test_params = {
                    "choice": "test_choice",
                    "cache": True,
                    "cache_index": 0,  # Use 0 since we only have 1 message
                    "custom_param": "custom_value",
                }

                # Call invoke with parameters
                llm.invoke(messages, **test_params)

                # Verify parameters were preserved
                for key, value in test_params.items():
                    assert fallback_params.get(key) == value


if __name__ == "__main__":
    pytest.main([__file__])
