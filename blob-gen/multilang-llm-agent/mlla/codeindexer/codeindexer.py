import asyncio
import dataclasses
import json
import threading
import time
from pathlib import Path
from typing import Awaitable, Dict, List, Set, Tuple, Union

from loguru import logger
from redis import Redis
from redis.lock import Lock

from .parser import (
    BaseParser,
    CIFunctionRes,
    CppParser,
    InternalFunctionRes,
    JavaParser,
)


@dataclasses.dataclass
class ParserConfig:
    extensions: list[str]
    parser: type[BaseParser]


class CodeIndexer:
    cp_name: str

    def __init__(
        self,
        global_redis: Redis,
        indexing_wait_time: int = 1,
        indexing_timeout: int = 600,
        lock_timeout: int = 5,  # Shorter lock timeout with active extension
    ):
        self.redis = global_redis
        self.ext_to_parser: dict[str, type[BaseParser]] = self._build_language_config()
        self.indexing_wait_time = indexing_wait_time
        self.indexing_timeout = indexing_timeout
        self.lock_timeout = lock_timeout
        self._redis_lock: Lock | None = None
        self._lock_watchdog_thread = None
        self._lock_watchdog_event = threading.Event()

    def _build_language_config(self) -> Dict[str, type[BaseParser]]:
        _language_config = {
            "c": ParserConfig(extensions=[".c", ".h"], parser=CppParser),
            "jvm": ParserConfig(extensions=[".java"], parser=JavaParser),
            "c++": ParserConfig(
                extensions=[".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx"],
                parser=CppParser,
            ),
        }
        ext_to_parser = {
            ext: config.parser
            for config in _language_config.values()
            for ext in config.extensions
        }
        return ext_to_parser

    async def _parse_file(
        self, file_path: Path, parser: type[BaseParser]
    ) -> Tuple[Dict[str, InternalFunctionRes], Dict[str, List[InternalFunctionRes]]]:
        return await parser().parse_file(file_path)

    async def index_project(
        self,
        cp_name: str,
        index_paths: Union[Path, list[Path]],  # To support backward-compatibility.
        language: str,
        overwrite: bool = False,
    ):
        # DK: why don't we just make this as a wrapper of build_index?
        if isinstance(index_paths, Path):
            index_paths = [index_paths]
        await self.build_index(cp_name, index_paths, language, overwrite)

    async def build_index(
        self,
        cp_name: str,
        index_paths: list[Path],
        language: str = "",
        overwrite: bool = False,
    ):
        """Build index for the project with improved race condition handling."""
        self.setup_project(cp_name)
        assert self.proj_name

        logger.debug(f"Start indexing loop for {self.proj_name}")

        # Create Redis lock with non-blocking acquire
        self._redis_lock = self.redis.lock(
            name=self.lock_key,
            timeout=self.lock_timeout,
            blocking=False,
            thread_local=False,
        )

        start_time = asyncio.get_event_loop().time()

        try:
            while True:
                # Try to acquire the lock without blocking
                if self._redis_lock and self._redis_lock.acquire():
                    logger.info(f"Acquired indexing lock for {self.proj_name}")

                    self._start_lock_watchdog()

                    if overwrite or not self.redis.exists(self.done_key):
                        logger.debug(f"Start indexing {self.cp_name}")
                        await self._process_indexing_within_lock(index_paths)
                        logger.debug(f"Project {self.proj_name} indexed successfully")
                    else:
                        logger.debug(f"Project {self.proj_name} already indexed")

                    # Always release the lock if we own it
                    if self._redis_lock and self._redis_lock.owned():
                        self._redis_lock.release()

                    self._stop_lock_watchdog()

                    return True

                # Check timeout before waiting
                current_time = asyncio.get_event_loop().time()
                if current_time - start_time > self.indexing_timeout:
                    logger.error("Timeout waiting for lock")
                    raise RuntimeError(
                        f"Timeout waiting for indexing lock on {self.proj_name}"
                    )

                # Wait for ongoing indexing
                logger.debug(f"Waiting for ongoing indexing of {self.proj_name}")
                await asyncio.sleep(self.indexing_wait_time)

        except Exception as e:
            logger.error(f"Error during indexing: {e}")
            # import traceback

            # logger.error(traceback.format_exc())

            # Ensure lock is released in case of any error
            if self._redis_lock and self._redis_lock.owned():
                self._redis_lock.release()
            self._stop_lock_watchdog()

            raise RuntimeError(f"Error during indexing: {e}")

    def _cleanup_indexed_data(self):
        try:
            logger.debug(f"Cleaning old data for project {self.cp_name}")
            # Delete the main hash that contains all function data
            self.redis.delete(self.proj_name)

            # Delete all candidate sets (but not metadata keys with :)
            candidate_keys = self.redis.keys(f"{self.proj_name}-*")
            if candidate_keys and isinstance(candidate_keys, list):
                self.redis.delete(*candidate_keys)

            # Delete done flag
            self.redis.delete(self.done_key)

        except Exception as e:
            logger.warning(f"Error cleaning up indexed data: {e}")
            raise RuntimeError(f"Error cleaning up indexed data: {e}")

    async def _process_indexing_within_lock(self, index_paths: list[Path]):
        try:
            self._cleanup_indexed_data()

            # Collect files
            files_and_parsers: list[tuple[Path, type[BaseParser]]] = []
            supported_extensions = set(self.ext_to_parser.keys())
            for index_path in index_paths:
                files_and_parsers.extend(
                    [
                        (f, self.ext_to_parser[f.suffix])
                        for f in index_path.rglob("*")
                        if f.suffix in supported_extensions
                    ]
                )
            logger.debug(f"Indexing {len(files_and_parsers)} files")

            if not files_and_parsers:
                # For empty projects, just mark as done and return
                self.redis.set(self.done_key, "1")
                logger.debug(f"No files to index in {self.proj_name}")
                return

            # Parse files
            tasks = [
                asyncio.create_task(self._parse_file(_file, _parser))
                for _file, _parser in files_and_parsers
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Write all data in a single pipeline
            success = True
            with self.redis.pipeline() as pipe:
                for (file, _), result in zip(files_and_parsers, results):
                    if isinstance(result, Exception):
                        logger.warning(
                            f"[CodeIndexer] Error processing {file}: {str(result)}"
                        )
                        success = False
                        continue

                    if not isinstance(result, tuple):
                        logger.error(
                            f"[CodeIndexer] Invalid result type: {type(result)}:"
                            f" {result}"
                        )
                        success = False
                        continue

                    hash_mapped_data, set_mapped_data = result
                    for key, data in hash_mapped_data.items():
                        # Write directly to final hash
                        pipe.hset(
                            self.proj_name,
                            key,
                            json.dumps(dataclasses.asdict(data)),
                        )
                    for key, _data in set_mapped_data.items():
                        for datum in _data:
                            # Write directly to final set
                            pipe.sadd(
                                f"{self.proj_name}-{key}",
                                json.dumps(dataclasses.asdict(datum)),
                            )

                pipe.execute()

            self.redis.set(self.done_key, "1")

            if not success:
                logger.error("[CodeIndexer] Some files failed to parse")

            logger.debug(f"Indexing finished {self.proj_name}")

        except Exception as e:
            logger.error(f"[CodeIndexer] Error during indexing: {e}")

            self._cleanup_indexed_data()

            raise RuntimeError(f"Error during indexing: {e}")

    def _start_lock_watchdog(self):
        """Start a background thread to extend the lock periodically."""
        self._lock_watchdog_event.clear()

        def extend_lock():
            while not self._lock_watchdog_event.is_set():
                try:
                    # Only extend if we still own the lock
                    if self._redis_lock and self._redis_lock.owned():
                        if not self._redis_lock.extend(
                            self.lock_timeout, replace_ttl=True
                        ):
                            logger.warning("Lost lock ownership, stopping extension")
                            break
                        time.sleep(self.lock_timeout / 2)  # Extend before expiration
                    else:
                        logger.warning("Lost lock ownership, stopping extension")
                        break
                except Exception as e:
                    logger.warning(f"Error extending lock: {e}")
                    break

        self._lock_watchdog_thread = threading.Thread(target=extend_lock)
        self._lock_watchdog_thread.daemon = (
            True  # Thread will exit when main thread exits
        )
        self._lock_watchdog_thread.start()

    def _stop_lock_watchdog(self):
        self._lock_watchdog_event.set()
        if self._lock_watchdog_thread:
            self._lock_watchdog_thread.join()

    def setup_project(self, cp_name: str):
        """Setup project by initializing project-related variables."""
        self.cp_name = cp_name
        self.proj_name = f"{cp_name}-code-index"
        self.temp_name = f"flag-temp:{self.proj_name}"
        self.done_key = f"flag-done:{self.proj_name}"
        self.lock_key = f"flag-indexing:{self.proj_name}"

    async def is_indexing_done(self) -> None:
        """Wait for indexing to complete if in progress."""
        if self.cp_name is None:
            raise RuntimeError("setup_project must be called first")

        # Check if already indexed successfully
        if self.redis.exists(self.done_key):
            return

        start_time = asyncio.get_event_loop().time()

        while True:
            # Check timeout
            current_time = asyncio.get_event_loop().time()
            if current_time - start_time > self.indexing_timeout:
                raise RuntimeError(
                    "Indexing wait timeout exceeded. Try other tool or wait "
                    "for indexing to complete."
                )

            # Wait until indexing is done.
            logger.debug(f"Waiting for indexing of {self.proj_name}")
            await asyncio.sleep(self.indexing_wait_time)

            if self.redis.exists(self.done_key):
                # Print the log for the first time.
                logger.info("Indexing is done. Start searching.")
                return

    async def search_function(
        self, function_name: str, type_only_params: bool = False
    ) -> List[CIFunctionRes]:
        """Search for a function by exact name."""

        async def fetch_data(data: Awaitable[str | None]) -> str | None:
            return await data

        await self.is_indexing_done()
        func_data = self.redis.hget(self.proj_name, function_name)

        if isinstance(func_data, Awaitable):
            func_data = await fetch_data(func_data)
        if func_data is None:
            return await self.search_candidates(function_name, type_only_params)

        if isinstance(func_data, bytes):
            func_data = func_data.decode("utf-8")
        data: Dict = json.loads(func_data)
        name = data.pop("type_only_func_name", None)
        if type_only_params and name:
            data["func_name"] = name
        res = CIFunctionRes(**data)

        return [res]

    async def search_candidates(
        self, function_name: str, type_only_params: bool = False
    ) -> List[CIFunctionRes]:
        """Search for functions that could be candidates for the given name."""

        async def fetch_data(data: Awaitable[Set]) -> Set:
            return await data

        await self.is_indexing_done()
        key = f"{self.proj_name}-{function_name}"

        candidates = []
        data = self.redis.smembers(key)
        if isinstance(data, Awaitable):
            data = await fetch_data(data)

        for d in data:
            if isinstance(d, bytes):
                raw_dict = d.decode("utf-8")
            dict_data: Dict = json.loads(raw_dict)
            name = dict_data.pop("type_only_func_name", None)
            if type_only_params and name:
                dict_data["func_name"] = name
            res = CIFunctionRes(**dict_data)
            candidates.append(res)

        return candidates
