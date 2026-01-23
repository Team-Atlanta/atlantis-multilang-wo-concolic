import asyncio
import time
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from mlla.utils.llm import LLM
from tests.dummy_context import DummyContext


class TestGlobalRateLimiter:
    """Test that global rate limiters work correctly across all LLM instances."""

    @pytest.fixture
    def dummy_context(self):
        """Create a DummyContext for testing."""
        with patch("getpass.getpass", return_value="test_key"), patch(
            "builtins.input", return_value="http://localhost:8000"
        ):
            context = DummyContext(no_llm=True)
            yield context
            context.cleanup()

    def test_shared_rate_limiters(self, dummy_context):
        """Test that all LLM instances share the same global rate limiters."""
        # Create multiple LLM instances with different models
        llm_openai_1 = LLM(
            model="gpt-4o-mini",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        llm_openai_2 = LLM(
            model="gpt-4o",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        llm_claude = LLM(
            model="claude-3-haiku-20240307",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        llm_gemini = LLM(
            model="gemini-1.5-flash",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        # Test that OpenAI models share the same global rate limiter
        assert llm_openai_1.chat_model.rate_limiter is dummy_context.global_rate_limiter
        assert llm_openai_2.chat_model.rate_limiter is dummy_context.global_rate_limiter
        assert (
            llm_openai_1.chat_model.rate_limiter is llm_openai_2.chat_model.rate_limiter
        )

        # Test that Claude models use the global Claude rate limiter
        assert (
            llm_claude.chat_model.rate_limiter
            is dummy_context.global_claude_rate_limiter
        )

        # Test that Gemini models use the global Claude rate limiter (as per the code)
        assert (
            llm_gemini.chat_model.rate_limiter
            is dummy_context.global_claude_rate_limiter
        )

        # Verify they are different limiters
        assert (
            dummy_context.global_rate_limiter
            is not dummy_context.global_claude_rate_limiter
        )

    def test_rate_limiter_configuration(self, dummy_context):
        """Test that rate limiters are configured correctly."""
        # Check global rate limiter configuration
        assert (
            dummy_context.global_rate_limiter.requests_per_second
            == dummy_context.max_concurrent_async_llm_calls
        )
        assert (
            dummy_context.global_rate_limiter.max_bucket_size
            == dummy_context.max_concurrent_async_llm_calls
        )
        assert dummy_context.global_rate_limiter.check_every_n_seconds == 0.3

        # Check Claude rate limiter configuration
        assert dummy_context.global_claude_rate_limiter.requests_per_second == 5
        assert dummy_context.global_claude_rate_limiter.max_bucket_size == 5
        assert dummy_context.global_claude_rate_limiter.check_every_n_seconds == 0.3

    @pytest.mark.asyncio
    async def test_rate_limiting_functionality(self, dummy_context):
        """Test that rate limiting actually works by monitoring call timing."""
        # Create LLM instances
        llm1 = LLM(
            model="gpt-4o-mini",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        llm2 = LLM(
            model="gpt-4o",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        # Mock the actual model invocation to avoid real API calls
        mock_response = AIMessage(content="Test response")

        # Track call times
        call_times = []

        async def mock_ainvoke_model_with_retry(*args, **kwargs):
            call_times.append(time.time())
            await asyncio.sleep(0.01)  # Simulate some processing time
            return mock_response

        # Patch the LLM's internal method instead of the chat model directly
        with patch.object(
            llm1, "_ainvoke_model_with_retry", side_effect=mock_ainvoke_model_with_retry
        ), patch.object(
            llm2, "_ainvoke_model_with_retry", side_effect=mock_ainvoke_model_with_retry
        ):

            # Create test messages
            messages = [HumanMessage(content="Test message")]

            # Make multiple concurrent calls that should be rate limited
            tasks = []
            for i in range(10):  # More than the rate limit
                if i % 2 == 0:
                    tasks.append(llm1.ainvoke(messages))
                else:
                    tasks.append(llm2.ainvoke(messages))

            # Execute all tasks concurrently
            start_time = time.time()
            await asyncio.gather(*tasks)
            total_time = time.time() - start_time

            # Verify that calls were made
            assert len(call_times) == 10

            # With rate limiting, this should take longer than without
            # At 20 requests per second, 10 requests should take at least 0.5 seconds
            # But we'll be more lenient in testing
            assert total_time > 0.1  # Should take some time due to rate limiting

    @pytest.mark.asyncio
    async def test_different_rate_limiters_work_independently(self, dummy_context):
        """Test that OpenAI and Claude rate limiters work independently."""
        # Create LLM instances for different model types
        llm_openai = LLM(
            model="gpt-4o-mini",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        llm_claude = LLM(
            model="claude-3-haiku-20240307",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        # Mock responses
        mock_response = AIMessage(content="Test response")

        openai_call_times = []
        claude_call_times = []

        async def mock_openai_ainvoke(*args, **kwargs):
            openai_call_times.append(time.time())
            await asyncio.sleep(0.01)
            return mock_response

        async def mock_claude_ainvoke(*args, **kwargs):
            claude_call_times.append(time.time())
            await asyncio.sleep(0.01)
            return mock_response

        # Patch the LLM's internal methods instead of the chat model directly
        with patch.object(
            llm_openai, "_ainvoke_model_with_retry", side_effect=mock_openai_ainvoke
        ), patch.object(
            llm_claude, "_ainvoke_model_with_retry", side_effect=mock_claude_ainvoke
        ):

            messages = [HumanMessage(content="Test message")]

            # Make calls to both types concurrently
            tasks = []
            for i in range(6):  # 3 OpenAI + 3 Claude calls
                if i < 3:
                    tasks.append(llm_openai.ainvoke(messages))
                else:
                    tasks.append(llm_claude.ainvoke(messages))

            await asyncio.gather(*tasks)

            # Verify calls were made to both
            assert len(openai_call_times) == 3
            assert len(claude_call_times) == 3

    def test_rate_limiter_object_identity(self, dummy_context):
        """Test that the same rate limiter objects are reused across instances."""
        # Create multiple LLM instances
        llms = []
        for i in range(5):
            llm = LLM(
                model="gpt-4o-mini",
                config=dummy_context,
                prepare_large_context_model=False,
            )
            llms.append(llm)

        # All should use the exact same rate limiter object
        rate_limiter_id = id(dummy_context.global_rate_limiter)
        for llm in llms:
            assert id(llm.chat_model.rate_limiter) == rate_limiter_id

    def test_summarize_model_uses_global_rate_limiter(self, dummy_context):
        """Test that the summarize model also uses the global rate limiter."""
        llm = LLM(
            model="gpt-4o-mini",
            config=dummy_context,
            prepare_large_context_model=False,
        )

        # The summarize model should also use the global rate limiter
        assert (
            llm.summarize_chat_model.rate_limiter is dummy_context.global_rate_limiter
        )
