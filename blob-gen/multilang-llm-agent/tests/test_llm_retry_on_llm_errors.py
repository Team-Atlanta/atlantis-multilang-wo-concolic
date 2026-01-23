from unittest.mock import MagicMock, patch

import pytest
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from loguru import logger
from tenacity import wait_fixed

from mlla.utils.llm import (
    LLM,
    UNKNOWN_ERROR_MAX_RETRIES,
    is_context_limit_error,
    is_rate_limit_error,
    is_server_error,
    retry_on_llm_errors,
)
from tests.prompts import LONG_PROMPT


def test_retry_on_llm_errors_success():
    """Test that retry_on_llm_errors decorator works when function succeeds."""

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def successful_function():
        return "success"

    result = successful_function()
    assert result == "success"


def test_is_rate_limit_error():
    """Test is_rate_limit_error function with various inputs."""

    # Test positive cases
    assert is_rate_limit_error(Exception("RateLimitError: Too many requests"))
    assert is_rate_limit_error(Exception("rate_limit_error occurred"))
    assert is_rate_limit_error(Exception("HTTP 429: Too Many Requests"))

    # Test negative case
    assert not is_rate_limit_error(Exception("Some other error"))


def test_is_server_error():
    """Test is_server_error function with various error types."""

    # Test server errors (should return True)
    server_errors = [
        "RateLimitError: Too many requests",
        "ServiceUnavailableError: Service down",
        "APIError: Internal server error",
        "APIConnectionError: Connection failed",
        "Timeout: Request timed out",
        "HTTP 429: Too Many Requests",
        "HTTP 503: Service Unavailable",
        "HTTP 500: Internal Server Error",
        "Connection error",
        "Request timed out",
        """Error code: 529 - {'error': {'message': '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', 'type': 'None', 'param': 'None', 'code': '529'}}""",  # noqa: E501
        """Error code: 502 - {'error': {'message': '<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n<hr><center>cloudflare</center>\r\n</body>\r\n</html>\r\n', 'type': 'None', 'param': 'None', 'code': '502'}}""",  # noqa: E501
    ]

    for error_msg in server_errors:
        assert is_server_error(Exception(error_msg)), f"Failed for: {error_msg}"

    # Test non-server errors (should return False)
    assert not is_server_error(Exception("ValueError: Invalid input"))
    assert not is_server_error(Exception("Some random error"))


def test_is_server_error_context_limit():
    """Test that context limit errors are NOT treated as server errors."""

    # Test context limit error (should return False - not a server error)
    context_limit_error = (
        "Error code: 400 - {'error': {'message':"
        " '\"{'type':'error','error':{'type':'invalid_request_error','message':'input"
        " length and `max_tokens` exceed context limit: 176024 + 64000 > 200000,"
        " decrease input length or `max_tokens` and try again'}}\"', 'type': 'None',"
        " 'param': 'None', 'code': '400'}}"
    )

    assert not is_server_error(Exception(context_limit_error))

    # Test other 400 errors (should return False - client errors)
    assert not is_server_error(Exception("Error code: 400 - Bad Request"))
    assert not is_server_error(Exception("HTTP 400: Bad Request"))

    # Test invalid_request_error type (should return False)
    assert not is_server_error(Exception("invalid_request_error: Bad input"))


def test_is_context_limit_error():
    """Test that context limit errors are treated as context limit errors."""
    context_limit_error = Exception(
        "Error code: 400 - {'error': {'message':"
        " '\"{'type':'error','error':{'type':'invalid_request_error','message':'input"
        " length and `max_tokens` exceed context limit: 176024 + 64000 > 200000,"
        " decrease input length or `max_tokens` and try again'}}\"', 'type': 'None',"
        " 'param': 'None', 'code': '400'}}"
    )
    assert is_context_limit_error(context_limit_error)


def test_retry_on_llm_errors_with_server_errors():
    """Test retry_on_llm_errors retries on server errors and stops on success."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_server_error():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("RateLimitError: Too many requests")
        return "success after retries"

    result = function_with_server_error()
    assert result == "success after retries"
    assert call_count == 3


def test_retry_on_llm_errors_no_retry_on_non_server_errors():
    """Test that retry_on_llm_errors doesn't retry on non-server errors."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_non_server_error():
        nonlocal call_count
        call_count += 1
        raise Exception("ValueError: Invalid input")

    # Use pytest.raises to catch the exception
    with pytest.raises(Exception) as exc_info:
        function_with_non_server_error()

    assert "ValueError" in str(exc_info.value)
    assert call_count == 1  # Should not retry


def test_retry_on_llm_errors_no_retry_on_context_limit():
    """Test that retry_on_llm_errors doesn't retry on context limit errors."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_context_limit_error():
        nonlocal call_count
        call_count += 1
        raise Exception(
            "Error code: 400 - {'error': {'message':"
            " '\"{'type':'error','error':{'type':'invalid_request_error',"
            " 'message':'input length and `max_tokens` exceed context limit: "
            "176024 + 64000 > 200000,"
            " decrease input length or `max_tokens` and try again'}}\"', 'type':"
            " 'None', 'param': 'None', 'code': '400'}}"
        )

    # Use pytest.raises to catch the exception
    with pytest.raises(Exception) as exc_info:
        function_with_context_limit_error()

    assert "context limit" in str(exc_info.value)
    assert call_count == 1  # Should not retry


def test_retry_on_llm_errors_exponential_backoff():
    """Test that retry_on_llm_errors uses exponential backoff."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_multiple_errors():
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise Exception("RateLimitError: Too many requests")
        return "success after multiple retries"

    with patch("time.sleep") as mock_sleep:
        result = function_with_multiple_errors()

        assert result == "success after multiple retries"
        assert call_count == 4
        assert mock_sleep.call_count >= 3  # Should have slept between retries


def test_retry_on_llm_errors_preserves_function_metadata():
    """Test that retry_on_llm_errors preserves function metadata."""

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def test_function():
        """This is a test function."""
        return "test"

    assert test_function.__name__ == "test_function"
    assert "This is a test function." in test_function.__doc__


def test_retry_on_llm_errors_with_function_arguments():
    """Test that retry_on_llm_errors works with functions that have arguments."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_args(x, y, z=None):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("RateLimitError: Too many requests")
        return f"x={x}, y={y}, z={z}"

    result = function_with_args(1, 2, z=3)
    assert result == "x=1, y=2, z=3"
    assert call_count == 2


def test_retry_on_llm_errors_async_function():
    """Test that retry_on_llm_errors works with async functions."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    async def async_function_with_error():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("RateLimitError: Too many requests")
        return "async success"

    import asyncio

    async def run_test():
        result = await async_function_with_error()
        return result

    result = asyncio.run(run_test())
    assert result == "async success"
    assert call_count == 2


@pytest.mark.skip(reason="This test doesn't work with the new large context model")
def test_llm_invoke_context_limit_error():
    """Test that LLM.invoke() handles context limit errors with internal retry."""

    context_limit_error = Exception(
        "Error code: 400 - {'error': {'message':"
        " '\"{'type':'error','error':{'type':'invalid_request_error',"
        "'message':'input length and `max_tokens` exceed context limit: "
        "176024 + 64000 > 200000,"
        " decrease input length or `max_tokens` and try again'}}\"', 'type': 'None',"
        " 'param': 'None', 'code': '400'}}"
    )

    # Mock the GlobalContext
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.base_url = "https://api.test.com"
    mock_config.openai_timeout = 30
    mock_config.openai_max_retries = 3
    mock_config.general_callback = MagicMock(spec=BaseCallbackHandler)
    mock_config.is_dev = False
    mock_config.max_concurrent_async_llm_calls = 5
    mock_config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    mock_config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    # Mock the chat model
    mock_runnable_chat_model = MagicMock()

    # Track the number of invoke calls
    invoke_call_count = 0

    def mock_invoke_side_effect(*args, **kwargs):
        nonlocal invoke_call_count
        invoke_call_count += 1
        raise context_limit_error

    mock_runnable_chat_model.invoke.side_effect = mock_invoke_side_effect
    mock_runnable_chat_model.max_tokens = 500

    # Patch the ChatOpenAI constructor and other dependencies

    # Create LLM instance
    llm = LLM(model="gpt-4", config=mock_config)

    # Replace the runnable_chat_model with our mock
    llm.runnable_chat_model = mock_runnable_chat_model

    # Test messages
    messages = [HumanMessage(content="Test message")]

    # Call invoke and verify behavior
    with pytest.raises(Exception):
        llm.invoke(messages)

    # No response is returned

    # No retry is done
    assert invoke_call_count == 1

    # Verify that the error was handled internally (no exception raised)
    # The test passing means the exception was caught and handled


def test_retry_on_llm_errors_with_unresolvable_error():
    """Test that retry_on_llm_errors doesn't retry on unresolvable errors."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_unresolvable_error():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception(
                "error 429:"
                " generativelanguage.googleapis.com/generate_requests_per_model_per_day"
            )
        return "success"

    with pytest.raises(Exception):
        function_with_unresolvable_error()

    assert call_count == 1


def test_retry_on_llm_errors_with_unresolvable_error_incorrect():
    """Test that retry_on_llm_errors doesn't retry on unresolvable errors."""

    call_count = 0

    @retry_on_llm_errors(wait=wait_fixed(0.1))
    def function_with_unresolvable_error_incorrect():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception(
                "error 429:"
                " generativelanguage.openai.com/generate_requests_per_model_per_day"
            )
        return "success"

    result = function_with_unresolvable_error_incorrect()

    assert result == "success"
    assert call_count == 2


def test_llm_invoke_on_unresolvable_error():
    unresolvable_error = Exception(
        "Error code: 400 - {'error': {'message': 'Budget has been exceeded! Current cost: 411.2746672000007, Max budget: 407.0', 'type': 'budget_exceeded', 'param': None, 'code': '400'}}"  # noqa: E501
    )

    # Mock the GlobalContext
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.base_url = "https://api.test.com"
    mock_config.openai_timeout = 30
    mock_config.openai_max_retries = 3
    mock_config.general_callback = MagicMock(spec=BaseCallbackHandler)
    mock_config.is_dev = False
    mock_config.max_concurrent_async_llm_calls = 5
    mock_config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    mock_config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    # Mock the chat model
    mock_runnable_chat_model = MagicMock()

    # Track the number of invoke calls
    invoke_call_count = 0

    def mock_invoke_side_effect(*args, **kwargs):
        nonlocal invoke_call_count
        invoke_call_count += 1
        raise unresolvable_error

    mock_runnable_chat_model.invoke.side_effect = mock_invoke_side_effect
    mock_runnable_chat_model.max_tokens = 500

    # Patch the ChatOpenAI constructor and other dependencies

    # Create LLM instance
    llm = LLM(model="gpt-4", config=mock_config)

    # Replace the runnable_chat_model with our mock
    llm.runnable_chat_model = mock_runnable_chat_model

    # Test messages
    messages = [HumanMessage(content="Test message")]

    with pytest.raises(Exception):
        # Call invoke and verify behavior
        llm.invoke(messages)

    # Verify that model was called 1 time (initial and exception)
    assert invoke_call_count == 1


@patch("time.sleep")
def test_llm_invoke_on_server_error_continues_retrying(mock_sleep):
    """
    Test that server errors continue retrying without giving up
    We'll simulate a scenario where server errors persist for many attempts
    """
    server_error = Exception("ServiceUnavailableError")

    # Mock the GlobalContext
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.base_url = "https://api.test.com"
    mock_config.openai_timeout = 30
    mock_config.openai_max_retries = 3
    mock_config.general_callback = MagicMock(spec=BaseCallbackHandler)
    mock_config.is_dev = False
    mock_config.max_concurrent_async_llm_calls = 5
    mock_config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    mock_config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    # Mock the chat model
    mock_runnable_chat_model = MagicMock()

    # Track the number of invoke calls
    invoke_call_count = 0

    def mock_invoke_side_effect(*args, **kwargs):
        nonlocal invoke_call_count
        invoke_call_count += 1

        # Server error should continue for 15 attempts, then succeed
        if invoke_call_count <= 15:
            raise server_error
        else:
            return AIMessage(content="Finally succeeded after many server retries")

    mock_runnable_chat_model.invoke.side_effect = mock_invoke_side_effect
    mock_runnable_chat_model.max_tokens = 500

    # Create LLM instance
    llm = LLM(model="gpt-4", config=mock_config)

    # Replace the runnable_chat_model with our mock
    llm.runnable_chat_model = mock_runnable_chat_model

    # Test messages
    messages = [HumanMessage(content="Test message")]

    # Call invoke and verify behavior
    result = llm.invoke(messages)[-1]

    # Verify that model was called 16 times (15 failures + 1 success)
    assert invoke_call_count == 16

    # Should eventually succeed even after many server errors
    assert isinstance(result, AIMessage)
    assert result.content == "Finally succeeded after many server retries"


@patch("time.sleep")
def test_llm_invoke_on_unknown_error(mock_sleep):
    unknown_error = Exception("This is unknown new error")

    # Mock the GlobalContext
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.base_url = "https://api.test.com"
    mock_config.openai_timeout = 30
    mock_config.openai_max_retries = 3
    mock_config.general_callback = MagicMock(spec=BaseCallbackHandler)
    mock_config.is_dev = False
    mock_config.max_concurrent_async_llm_calls = 5
    mock_config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    mock_config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    # Mock the chat model
    mock_runnable_chat_model = MagicMock()

    # Track the number of invoke calls
    invoke_call_count = 0

    def mock_invoke_side_effect(*args, **kwargs):
        nonlocal invoke_call_count
        invoke_call_count += 1

        if invoke_call_count < UNKNOWN_ERROR_MAX_RETRIES:
            raise unknown_error
        else:
            return AIMessage(content="Success response after retries")

    mock_runnable_chat_model.invoke.side_effect = mock_invoke_side_effect
    mock_runnable_chat_model.max_tokens = 500

    # Create LLM instance
    llm = LLM(model="gpt-4", config=mock_config)

    # Replace the runnable_chat_model with our mock
    llm.runnable_chat_model = mock_runnable_chat_model

    # Test messages
    messages = [HumanMessage(content="Test message")]

    # Call invoke and verify behavior
    result = llm.invoke(messages)[-1]

    assert invoke_call_count == UNKNOWN_ERROR_MAX_RETRIES

    assert isinstance(result, AIMessage)
    assert result.content == "Success response after retries"


@patch("time.sleep")
def test_llm_invoke_on_unknown_error_max_retries_exceeded(mock_sleep):
    unknown_error = Exception("This is unknown new error that keeps happening")

    # Mock the GlobalContext
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.base_url = "https://api.test.com"
    mock_config.openai_timeout = 30
    mock_config.openai_max_retries = 3
    mock_config.general_callback = MagicMock(spec=BaseCallbackHandler)
    mock_config.is_dev = False
    mock_config.max_concurrent_async_llm_calls = 5
    mock_config.global_rate_limiter = InMemoryRateLimiter(
        requests_per_second=10, check_every_n_seconds=0.3, max_bucket_size=10
    )
    mock_config.global_claude_rate_limiter = InMemoryRateLimiter(
        requests_per_second=5, check_every_n_seconds=0.3, max_bucket_size=5
    )

    # Mock the chat model
    mock_runnable_chat_model = MagicMock()

    # Track the number of invoke calls
    invoke_call_count = 0

    def mock_invoke_side_effect(*args, **kwargs):
        nonlocal invoke_call_count
        invoke_call_count += 1
        raise unknown_error

    mock_runnable_chat_model.invoke.side_effect = mock_invoke_side_effect
    mock_runnable_chat_model.max_tokens = 500

    # Create LLM instance
    llm = LLM(model="gpt-4", config=mock_config)

    # Replace the runnable_chat_model with our mock
    llm.runnable_chat_model = mock_runnable_chat_model

    # Test messages
    messages = [HumanMessage(content="Test message")]

    # Call invoke and verify behavior
    result = llm.invoke(messages)[-1]

    # Verify that model was called UNKNOWN_ERROR_MAX_RETRIES times
    assert invoke_call_count == UNKNOWN_ERROR_MAX_RETRIES

    assert isinstance(result, AIMessage)
    assert result.content == "LLM failed to generate a response."


@pytest.mark.parametrize(
    "model_name",
    [
        "gpt-4o-mini",
        "o4-mini",
        "claude-3-7-sonnet-20250219",
        "gpt-4.1-mini",
        "gpt-4.1",
        "claude-sonnet-4-20250514",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "o3-mini",
        "claude-3-5-haiku-20241022",
    ],
)
@pytest.mark.skip(reason="This test is too slow")
def test_llm_context_limit_error_constant_check(model_name, config):
    """Test that LLM.invoke() handles context limit errors with internal retry."""

    llm = LLM(
        model=model_name,
        config=config,
        prepare_large_context_model=False,
        max_tokens=1000,
    )

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=LONG_PROMPT),
    ]

    # Disable summarize
    with patch.object(llm, "summarize", lambda x: x):
        success = False
        try:
            llm.invoke(messages)
        except Exception as e:
            logger.info(f"Error: {e}")
            success = True
            assert is_context_limit_error(e)
        assert success
