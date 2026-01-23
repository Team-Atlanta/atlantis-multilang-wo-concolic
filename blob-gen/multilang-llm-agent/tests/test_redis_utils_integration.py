import subprocess
import threading
import time

import pytest

from mlla.utils.redis_utils import init_redis_with_retry


def test_real_redis_connection(redis_container):
    """Test connection to real Redis server."""
    redis_client = init_redis_with_retry(
        redis_host=redis_container.host, retry_interval=1, socket_timeout=1
    )

    # Verify connection works
    assert redis_client.ping()

    # Test basic operations
    redis_client.set("test_key", "test_value")
    assert redis_client.get("test_key") == b"test_value"


def test_real_redis_retry_on_container_restart(redis_container):
    """Test retry behavior when Redis container is restarted."""
    redis_client = init_redis_with_retry(
        redis_host=redis_container.host, retry_interval=1, socket_timeout=1
    )

    # Initial connection should work
    assert redis_client.ping()

    # Stop Redis container
    subprocess.run(["docker", "stop", redis_container.container], check=True)

    # Start new connection attempt in background
    connection_error = None
    connection_success = False

    def try_connect():
        nonlocal connection_error, connection_success
        try:
            client = init_redis_with_retry(
                redis_host=redis_container.host, retry_interval=1, socket_timeout=1
            )
            assert client.ping()
            connection_success = True
        except Exception as e:
            connection_error = e

    thread = threading.Thread(target=try_connect)
    thread.start()

    # Wait a bit then restart container
    time.sleep(3)
    subprocess.run(["docker", "start", redis_container.container], check=True)

    # Wait for connection attempt to complete
    thread.join(timeout=10)
    assert not thread.is_alive(), "Connection attempt timed out"
    assert connection_success, f"Connection failed: {connection_error}"


def test_real_redis_max_retries(redis_container):
    """Test max retries with real Redis server."""
    # Stop Redis container
    subprocess.run(["docker", "stop", redis_container.container], check=True)

    # Try to connect with max retries
    with pytest.raises(RuntimeError) as exc_info:
        init_redis_with_retry(
            redis_host=redis_container.host,
            retry_interval=1,
            max_retries=2,
            socket_timeout=1,
        )

    assert "Failed to connect to Redis" in str(exc_info.value)

    # Restart container for cleanup
    subprocess.run(["docker", "start", redis_container.container], check=True)
