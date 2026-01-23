from unittest.mock import MagicMock, call, patch

import pytest
from loguru import logger
from redis import Redis, RedisError

from mlla.utils.redis_utils import (
    REDIS_DEFAULT_PORT,
    init_redis_with_retry,
    parse_redis_host,
)


@pytest.mark.parametrize(
    "test_input,expected",
    [
        # Test host:port format
        ("localhost:6379", ("localhost", 6379)),
        ("127.0.0.1:6380", ("127.0.0.1", 6380)),
        # Test host only format (uses default port)
        ("localhost", ("localhost", REDIS_DEFAULT_PORT)),
        ("127.0.0.1", ("127.0.0.1", REDIS_DEFAULT_PORT)),
        # Test redis://host:port format
        ("redis://localhost:6379", ("localhost", 6379)),
        ("redis://custom.redis.server:2512", ("custom.redis.server", 2512)),
        ("redis://asasleifhalsiefhalies:2512", ("asasleifhalsiefhalies", 2512)),
        # Test redis:host:port format (should treat redis: as part of hostname)
        ("redis:localhost:6379", ("redis:localhost", 6379)),
        ("redis:custom.server:2512", ("redis:custom.server", 2512)),
        # Test edge cases
        ("redis://localhost", ("localhost", REDIS_DEFAULT_PORT)),  # No port with scheme
        (
            "redis://localhost:",
            ("localhost", REDIS_DEFAULT_PORT),
        ),  # Empty port with scheme
        ("localhost:", ("localhost", REDIS_DEFAULT_PORT)),  # Empty port without scheme
        ("redis://127.0.0.1", ("127.0.0.1", REDIS_DEFAULT_PORT)),  # IPv4 no port
    ],
)
def test_parse_redis_host(test_input, expected):
    """Test parsing of various Redis host string formats."""
    assert parse_redis_host(test_input) == expected


@pytest.mark.parametrize(
    "test_input,expected_host",
    [
        # Invalid port formats
        ("localhost:invalid", "localhost"),
        ("redis://localhost:invalid", "localhost"),
        ("redis:localhost:invalid", "redis:localhost"),
        # Out of range ports
        ("redis://localhost:99999", "localhost"),  # Port number too large
        ("redis://localhost:-1", "localhost"),  # Negative port
        ("redis://localhost:0", "localhost"),  # Invalid port 0
        # IPv4 with invalid ports
        ("127.0.0.1:invalid", "127.0.0.1"),
        ("redis://127.0.0.1:99999", "127.0.0.1"),
    ],
)
def test_parse_redis_host_invalid_port(test_input, expected_host):
    """Test parsing of Redis host strings with invalid ports."""
    host, port = parse_redis_host(test_input)
    assert port == REDIS_DEFAULT_PORT
    assert host == expected_host


def test_redis_retry_success(redis_host):
    """Test that init_redis_with_retry retries until Redis is available."""
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()

    # First two attempts fail, third succeeds
    mock_redis_instance.ping.side_effect = [
        RedisError("Connection refused"),
        RedisError("Connection refused"),
        True,
    ]
    mock_redis.return_value = mock_redis_instance

    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        with patch("mlla.utils.redis_utils.time.sleep") as mock_sleep:
            redis_client = init_redis_with_retry(
                redis_host=redis_host,
                retry_interval=1,
                socket_timeout=1,  # 1 second for faster testing
            )

    host, port = redis_host.rsplit(":", 1)
    port = int(port)

    # Should have attempted connection 3 times
    assert mock_redis.call_count == 3
    # Should have slept twice (after first two failures)
    assert mock_sleep.call_count == 2
    # Should have returned the Redis client
    assert redis_client == mock_redis_instance
    # Verify Redis was initialized with correct parameters and ping was called
    expected_calls = []
    for _ in range(3):
        expected_calls.extend(
            [call(host=host, port=port, db=0, socket_connect_timeout=1), call().ping()]
        )
    mock_redis.assert_has_calls(expected_calls)


def test_redis_retry_with_max_retries(redis_host):
    """Test that init_redis_with_retry respects max_retries."""
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()
    mock_redis_instance.ping.side_effect = RedisError("Connection refused")
    mock_redis.return_value = mock_redis_instance

    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        with patch("mlla.utils.redis_utils.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError) as exc_info:
                init_redis_with_retry(
                    redis_host=redis_host,
                    retry_interval=1,  # 1 second for faster testing
                    max_retries=3,  # Only try 3 times
                    socket_timeout=1,
                )

    host, port = redis_host.rsplit(":", 1)
    port = int(port)

    # Should have attempted connection 3 times
    assert mock_redis.call_count == 3
    # Should have slept twice (after first two failures)
    assert mock_sleep.call_count == 2
    # Should have raised RuntimeError
    assert "Failed to connect to Redis" in str(exc_info.value)
    # Verify Redis was initialized with correct parameters and ping was called
    expected_calls = []
    for _ in range(3):
        expected_calls.extend(
            [call(host=host, port=port, db=0, socket_connect_timeout=1), call().ping()]
        )
    mock_redis.assert_has_calls(expected_calls)


def test_redis_retry_with_delayed_start(redis_host):
    """Test that init_redis_with_retry waits for Redis to start."""
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()

    # First three attempts fail, fourth succeeds
    mock_redis_instance.ping.side_effect = [
        RedisError("Connection refused"),
        RedisError("Connection refused"),
        RedisError("Connection refused"),
        True,
    ]
    mock_redis.return_value = mock_redis_instance

    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        with patch("mlla.utils.redis_utils.time.sleep") as mock_sleep:
            redis_client = init_redis_with_retry(
                redis_host=redis_host,
                retry_interval=1,
                socket_timeout=1,  # 1 second for faster testing
            )

    host, port = redis_host.rsplit(":", 1)
    port = int(port)

    # Should have attempted connection 4 times
    assert mock_redis.call_count == 4
    # Should have slept 3 times (after first three failures)
    assert mock_sleep.call_count == 3
    # Should have returned the Redis client
    assert redis_client == mock_redis_instance
    # Verify Redis was initialized with correct parameters and ping was called
    expected_calls = []
    for _ in range(4):
        expected_calls.extend(
            [call(host=host, port=port, db=0, socket_connect_timeout=1), call().ping()]
        )
    mock_redis.assert_has_calls(expected_calls)
    # Verify sleep was called with correct interval
    mock_sleep.assert_called_with(1)


def test_redis_retry_logs_attempts(redis_host):
    """Test that init_redis_with_retry logs connection attempts."""
    # Setup loguru test handler
    log_messages = []

    def test_sink(message):
        log_messages.append(message.record["message"])

    test_handler_id = logger.add(test_sink, level="INFO")
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()

    # First attempt fails, second succeeds
    mock_redis_instance.ping.side_effect = [RedisError("Connection refused"), True]
    mock_redis.return_value = mock_redis_instance

    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        with patch("mlla.utils.redis_utils.time.sleep"):
            init_redis_with_retry(
                redis_host=redis_host,
                retry_interval=1,
                socket_timeout=1,  # 1 second for faster testing
            )

    try:
        # Check exact log messages and their order
        assert len(log_messages) >= 3  # Should have at least 3 log messages

        # First attempt
        assert f"Attempting to connect to Redis at: {redis_host}" == log_messages[0]
        assert (
            f"Failed to connect to Redis at {redis_host}: Connection refused"
            in log_messages[1]
        )

        # Second attempt
        assert f"Attempting to connect to Redis at: {redis_host}" == log_messages[2]
        assert "Successfully connected to Redis" in log_messages[3]
    finally:
        # Remove test handler
        logger.remove(test_handler_id)


def test_redis_retry_interval_respected(redis_host):
    """Test that retry_interval is respected between attempts."""
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()
    mock_redis_instance.ping.side_effect = [
        RedisError("Connection refused"),
        RedisError("Connection refused"),
        True,
    ]
    mock_redis.return_value = mock_redis_instance

    retry_interval = 5  # 5 seconds between retries
    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        with patch("mlla.utils.redis_utils.time.sleep") as mock_sleep:
            init_redis_with_retry(
                redis_host=redis_host, retry_interval=retry_interval, socket_timeout=1
            )

    host, port = redis_host.rsplit(":", 1)
    port = int(port)

    # Verify Redis was initialized with correct parameters and ping was called
    expected_calls = []
    for _ in range(3):
        expected_calls.extend(
            [call(host=host, port=port, db=0, socket_connect_timeout=1), call().ping()]
        )
    mock_redis.assert_has_calls(expected_calls)
    # Verify sleep was called with correct interval each time
    assert mock_sleep.call_count == 2
    mock_sleep.assert_has_calls([call(retry_interval), call(retry_interval)])


def test_redis_retry_with_invalid_port(redis_host):
    """Test that init_redis_with_retry uses default port for invalid port."""
    mock_redis = MagicMock(spec=Redis)
    mock_redis_instance = MagicMock()
    mock_redis_instance.ping.return_value = True
    mock_redis.return_value = mock_redis_instance

    host = redis_host.split(":")[0]
    invalid_host = f"{host}:invalid"

    with patch("mlla.utils.redis_utils.Redis", mock_redis):
        redis_client = init_redis_with_retry(
            redis_host=invalid_host, retry_interval=1, socket_timeout=1
        )

    # Should use default port when port is invalid
    mock_redis.assert_called_once_with(
        host=host,
        port=6379,  # Default port
        db=0,
        socket_connect_timeout=1,
    )
    assert redis_client == mock_redis_instance
