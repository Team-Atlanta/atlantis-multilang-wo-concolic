import argparse
import asyncio
from pathlib import Path

from loguru import logger

from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.utils.context import get_common_paths
from mlla.utils.cp import sCP
from mlla.utils.redis_utils import init_redis_with_retry


class CodeIndexerRunner:
    def run(self, redis_host: str | None, cp_path: str, db_index: int = 0) -> None:
        """Run the code indexer."""
        self._init_redis(redis_host, db_index)
        _, self.cp = sCP.from_cp_path(Path(cp_path))
        print(self.cp)
        self._init_code_indexer()

    def _init_redis(self, redis_host: str | None, db_index: int = 0) -> None:
        """Initialize Redis connection."""
        redis = init_redis_with_retry(redis_host, db=db_index)
        if not redis:
            raise RuntimeError(f"Redis is not set properly for {redis_host}")

        self.redis = redis
        host = self.redis.connection_pool.connection_kwargs["host"]
        port = self.redis.connection_pool.connection_kwargs["port"]
        self.redis_host = f"{host}:{port}"

    def _init_code_indexer(self) -> None:
        """Initialize code indexer and indexing the project."""
        self.code_indexer = CodeIndexer(self.redis)

        try:
            # Run the async function using asyncio.run
            index_paths = get_common_paths(self.cp.proj_path, self.cp.cp_src_path)
            asyncio.run(
                self.code_indexer.build_index(
                    self.cp.name, index_paths, self.cp.language
                )
            )
        except Exception as e:
            logger.error(f"Error during code indexing: {e}")
        finally:
            logger.info("Code indexing finished")


def main():
    parser = argparse.ArgumentParser(
        description="Main function for executing the code indexer individually."
    )

    parser.add_argument("--cp", required=True, help="Path to the CP directory.")

    parser.add_argument(
        "--redis",
        help="Redis server address (default: localhost or docker gateway)",
    )
    parser.add_argument(
        "--db-index",
        type=int,
        default=0,
        help="Redis database index (default: 0)",
    )
    args = parser.parse_args()

    cp_path = args.cp
    redis_addr = args.redis
    db_index = args.db_index

    runner = CodeIndexerRunner()
    runner.run(redis_addr, cp_path, db_index)


if __name__ == "__main__":
    main()
