import asyncio
import threading
import time

from loguru import logger


class SlotManager:
    """Handle slot acquisition and release for both sync and async."""

    def __init__(self, manager):
        self.manager = manager

    def __enter__(self):
        """Acquire a slot synchronously."""
        while True:
            with self.manager._sync_lock:
                if self.manager._current_concurrent < self.manager._max_concurrent:
                    self.manager._current_concurrent += 1
                    if (
                        self.manager._current_concurrent
                        > self.manager._max_concurrent / 2
                    ):
                        logger.debug(
                            "Acquired slot (sync). Total concurrent calls:"
                            f" {self.manager._current_concurrent}"
                        )
                    return self
            time.sleep(0.1)  # Small delay before checking again

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release the slot synchronously."""
        with self.manager._sync_lock:
            self.manager._current_concurrent -= 1
            if self.manager._current_concurrent > self.manager._max_concurrent / 2:
                logger.debug(
                    "Released slot (sync). Total concurrent calls:"
                    f" {self.manager._current_concurrent}"
                )

    async def __aenter__(self):
        """Acquire a slot asynchronously."""
        while True:
            async with self.manager._async_lock:
                if self.manager._current_concurrent < self.manager._max_concurrent:
                    self.manager._current_concurrent += 1
                    if (
                        self.manager._current_concurrent
                        > self.manager._max_concurrent / 2
                    ):
                        logger.debug(
                            "Acquired slot (async). Total concurrent calls:"
                            f" {self.manager._current_concurrent}"
                        )
                    return self
            await asyncio.sleep(0.1)  # Small delay before checking again

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release the slot asynchronously."""
        # Always decrement counter, even if event loop is closed
        try:
            async with self.manager._async_lock:
                self.manager._current_concurrent -= 1
                if self.manager._current_concurrent > self.manager._max_concurrent / 2:
                    logger.debug(
                        "Released slot (async). Total concurrent calls:"
                        f" {self.manager._current_concurrent}"
                    )
        except RuntimeError as e:
            # Fallback to sync lock for any runtime error
            with self.manager._sync_lock:
                self.manager._current_concurrent -= 1
                if self.manager._current_concurrent > self.manager._max_concurrent / 2:
                    logger.debug(
                        f"Released slot (sync fallback). Error: {e}.\n"
                        "Total concurrent calls: "
                        f"{self.manager._current_concurrent}"
                    )


# class ConcurrencyManager:
#     """For LLM calls using a shared counter with sync/async locks."""

#     def __init__(self, max_concurrent: int):
#         self._max_concurrent = max_concurrent
#         self._current_concurrent = (
#             0  # Shared counter for both sync and async operations
#         )
#         self._sync_lock = threading.Lock()  # For sync operations
#         self._async_lock = asyncio.Lock()  # For async operations

#     def __call__(self):
#         """Make the manager callable to create context managers."""
#         return SlotManager(self)


class ConcurrencyManager:
    def __init__(self, max_concurrent: int):
        self._sync_sem = threading.Semaphore(max_concurrent)
        self._async_sem = asyncio.Semaphore(max_concurrent)

    def __call__(self):
        return self

    def __enter__(self):
        self._sync_sem.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sync_sem.release()

    async def __aenter__(self):
        await self._async_sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._async_sem.release()
