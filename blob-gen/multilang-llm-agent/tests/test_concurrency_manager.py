import asyncio
import threading
import time

import pytest

from mlla.utils.concurrency_manager import ConcurrencyManager


def test_sync_context_manager():
    """Test synchronous context manager basic operation."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    with manager():
        assert manager._sync_sem._value == max_concurrent - 1
    assert manager._sync_sem._value == max_concurrent


def test_sync_multiple_contexts():
    """Test multiple synchronous contexts running concurrently."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    with manager():
        assert manager._sync_sem._value == max_concurrent - 1
        with manager():
            assert manager._sync_sem._value == max_concurrent - 2
        assert manager._sync_sem._value == max_concurrent - 1
    assert manager._sync_sem._value == max_concurrent


def test_sync_exception_handling():
    """Test synchronous context manager handles exceptions properly."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    try:
        with manager():
            assert manager._sync_sem._value == max_concurrent - 1
            raise ValueError("Test exception")
    except ValueError:
        pass
    assert manager._sync_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_async_context_manager():
    """Test asynchronous context manager basic operation."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    async with manager():
        assert manager._async_sem._value == max_concurrent - 1
    assert manager._async_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_async_multiple_contexts():
    """Test multiple asynchronous contexts running concurrently."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    async with manager():
        assert manager._async_sem._value == max_concurrent - 1
        async with manager():
            assert manager._async_sem._value == max_concurrent - 2
        assert manager._async_sem._value == max_concurrent - 1
    assert manager._async_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_mixed_sync_async():
    """Test that sync and async operations can work together correctly."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)
    order = []

    def sync_task():
        with manager():
            order.append("sync")
            time.sleep(0.1)  # Ensure overlap with async task

    async def async_task():
        async with manager():
            order.append("async")
            await asyncio.sleep(0.1)

    # Start sync task in thread
    thread = threading.Thread(target=sync_task)
    thread.start()

    # Run async task
    await async_task()
    thread.join()

    # Verify both tasks ran and cleaned up
    assert "sync" in order and "async" in order
    assert manager._sync_sem._value == max_concurrent
    assert manager._async_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_max_concurrent_limit():
    """Test that max_concurrent limit is strictly enforced."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)
    active = []
    nonlocal_max = {"value": 0}  # Using dict to modify from inner scope

    async def task():
        async with manager():
            active.append(1)
            nonlocal_max["value"] = max(len(active), nonlocal_max["value"])
            await asyncio.sleep(0.01)
            active.pop()

    # Try to run more tasks than allowed
    tasks = [task() for _ in range(5)]
    await asyncio.gather(*tasks)

    assert nonlocal_max["value"] <= 2  # Never exceeded limit
    assert manager._sync_sem._value == max_concurrent
    assert manager._async_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_async_cancellation():
    """Test that cancellation during async operation cleans up properly."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    async def task():
        async with manager():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # Context manager should still clean up
                raise

    t = asyncio.create_task(task())
    await asyncio.sleep(0.1)
    t.cancel()

    try:
        await t
    except asyncio.CancelledError:
        pass

    assert manager._async_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_event_loop_closure():
    """Test that event loop closure is handled gracefully."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)

    async def task():
        try:
            async with manager():
                # Simulate event loop closure by raising RuntimeError
                raise RuntimeError("Event loop is closed")
        except RuntimeError:
            pass

    await task()
    assert manager._async_sem._value == max_concurrent


def test_nested_error_propagation():
    """Test error propagation in nested contexts."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)
    error_order = []

    class CustomError(Exception):
        def __init__(self, name):
            self.name = name

    try:
        with manager():
            error_order.append("enter1")
            try:
                with manager():
                    error_order.append("enter2")
                    raise CustomError("inner")
            except CustomError:
                error_order.append("exit2")
                raise CustomError("outer")
    except CustomError:
        error_order.append("exit1")

    # Verify error propagation order
    assert error_order == ["enter1", "enter2", "exit2", "exit1"]
    assert manager._sync_sem._value == max_concurrent


@pytest.mark.asyncio
async def test_stress():
    """Stress test with both sync and async operations."""
    max_concurrent = 2
    manager = ConcurrencyManager(max_concurrent=max_concurrent)
    async_results = []
    sync_results = []

    current_in_sync = 0
    current_in_async = 0
    lock = threading.Lock()
    max_sync = 0
    max_async = 0
    max_total = 0

    async def async_task():
        nonlocal current_in_async, lock, max_async, max_total
        for _ in range(5):  # Each task does 5 rapid acquire/release
            async with manager():
                with lock:
                    current_in_async += 1
                    max_async = max(max_async, current_in_async)
                    max_total = max(max_total, current_in_sync + current_in_async)
                async_results.append(manager._async_sem._value)
                await asyncio.sleep(0.001)  # Very short sleep
                with lock:
                    current_in_async -= 1

    def sync_task():
        nonlocal current_in_sync, lock, max_sync, max_total
        for _ in range(5):  # Each thread does 5 rapid acquire/release
            with manager():
                with lock:
                    current_in_sync += 1
                    max_sync = max(max_sync, current_in_sync)
                    max_total = max(max_total, current_in_sync + current_in_async)
                sync_results.append(manager._sync_sem._value)
                time.sleep(0.001)  # Very short sleep
                with lock:
                    current_in_sync -= 1

    # Start sync tasks in threads
    threads = [threading.Thread(target=sync_task) for _ in range(5)]
    for t in threads:
        t.start()

    # Run async tasks
    tasks = [async_task() for _ in range(5)]
    await asyncio.gather(*tasks)

    # Wait for threads to complete
    for t in threads:
        t.join()

    assert manager._sync_sem._value == max_concurrent
    assert manager._async_sem._value == max_concurrent
    assert all(
        r <= max_concurrent and r >= 0 for r in async_results
    )  # Never exceeded limit
    assert all(
        r <= max_concurrent and r >= 0 for r in sync_results
    )  # Never exceeded limit
    assert max_sync <= max_concurrent
    assert max_async <= max_concurrent
    assert max_total <= max_concurrent * 2
