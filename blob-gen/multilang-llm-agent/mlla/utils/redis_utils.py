import os
import time
from typing import Optional

from loguru import logger
from redis import Redis, RedisError

from .cp import get_docker_gateway

REDIS_DEFAULT_HOST = "localhost"
REDIS_DEFAULT_PORT = 6379


def get_default_redis_host() -> str:
    """Get the default Redis host address with port."""
    host = os.getenv("CODE_INDEXER_REDIS_URL")
    if host:
        return host

    # always assume that we are inside the docker
    host = get_docker_gateway()
    return f"{host}:{REDIS_DEFAULT_PORT}"


def parse_redis_host(redis_host: str) -> tuple[str, int]:
    """Parse Redis host string into host and port components.

    Handles various formats:
    - host:port (e.g., "localhost:6379", "127.0.0.1:6379")
    - redis://host:port (e.g., "redis://localhost:6379")
    - redis:host:port (treats redis: as part of hostname)
    - host (uses default port)

    Returns:
        tuple: (host, port)
    """
    port = REDIS_DEFAULT_PORT

    # Remove scheme if present (e.g., redis://)
    if "://" in redis_host:
        redis_host = redis_host.split("://", 1)[1]

    # Handle empty port case
    if redis_host.endswith(":"):
        redis_host = redis_host[:-1]

    # Parse host and port
    if ":" in redis_host:
        host, port_str = redis_host.rsplit(":", 1)
        if port_str:  # Only try to parse if port string is not empty
            try:
                port_num = int(port_str)
                if 1 <= port_num <= 65535:  # Valid port range
                    port = port_num
                else:
                    logger.warning(
                        f"Port number {port_str} out of range (1-65535), using default"
                        f" port {port}"
                    )
            except ValueError:
                logger.warning(
                    f"Invalid port number '{port_str}', using default port {port}"
                )
    else:
        host = redis_host

    return host, port


def init_redis_with_retry(
    redis_host: Optional[str] = None,
    retry_interval: int = 1,
    max_retries: Optional[int] = None,
    socket_timeout: int = 10,
    db: int = 0,
) -> Redis:
    """Initialize Redis connection with retry mechanism."""
    if not redis_host:
        redis_host = get_default_redis_host()

    host, port = parse_redis_host(redis_host)
    redis_addr = f"{host}:{port}"
    retry_count = 0

    while max_retries is None or retry_count < max_retries:
        try:
            logger.info(f"Attempting to connect to Redis at: {redis_addr}")
            redis_client = Redis(
                host=host, port=port, db=db, socket_connect_timeout=socket_timeout
            )
            if not redis_client.ping():
                raise RedisError("Redis ping failed")
            logger.info(f"Successfully connected to Redis at: {redis_addr}")
            return redis_client
        except Exception as e:
            retry_count += 1
            error_msg = f"Failed to connect to Redis at {redis_addr}: {str(e)}"
            if max_retries is None or retry_count < max_retries:
                logger.warning(f"{error_msg}. Retrying in {retry_interval} seconds...")
                time.sleep(retry_interval)
            else:
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e

    error_msg = f"Failed to connect to Redis at {redis_addr} in {max_retries} trials"
    raise RuntimeError(error_msg)
