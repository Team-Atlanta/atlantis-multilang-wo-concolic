import asyncio
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest
from loguru import logger

from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.codeindexer.parser import BaseParser, InternalFunctionRes


class SlowParser(BaseParser):
    """A mock parser that simulates slow parsing."""

    async def parse_file(
        self, file_path: Path
    ) -> Tuple[Dict[str, InternalFunctionRes], Dict[str, List[InternalFunctionRes]]]:
        # Simulate slow parsing
        await asyncio.sleep(2)  # Longer delay to ensure lock contention

        content = file_path.read_text()

        # Fail on invalid content (for test_failed_indexing_cleanup)
        if "This will cause the parser to fail" in content:
            raise RuntimeError("Invalid file content")

        # Simple parsing: treat each line as a function
        functions = {}
        candidates: Dict[str, List[InternalFunctionRes]] = {}

        for i, line in enumerate(content.splitlines()):
            if line.strip():
                # Extract function number from content
                if "function content" in line:
                    try:
                        num = int(line.split()[-1])
                        name = f"func_{num}"
                        func = InternalFunctionRes(
                            func_name=name,
                            file_path=str(file_path),
                            start_line=i,
                            end_line=i,
                            func_body=line,
                        )
                        # Store with unique key that includes file path
                        unique_key = f"{name}:{file_path}"
                        functions[unique_key] = func
                        # Add to candidates using just the name
                        if name not in candidates:
                            candidates[name] = []
                        candidates[name].append(func)
                    except ValueError:
                        continue

        return functions, candidates


@pytest.fixture
def large_test_files(tmp_path):
    """Create a large number of test files to make indexing take longer."""
    for i in range(10):  # Create 10 files
        test_file = tmp_path / f"test_{i}.slow"
        # Each file has 100 lines
        content = "\n".join(f"function content {j}" for j in range(100))
        test_file.write_text(content)
    return tmp_path


@pytest.fixture
def slow_indexer(redis_client):
    """Create a CodeIndexer with the slow parser."""
    indexer = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )
    # Add our slow parser configuration
    indexer.ext_to_parser[".slow"] = SlowParser
    return indexer


@pytest.mark.asyncio
async def test_concurrent_indexing(redis_client, large_test_files):
    """Test multiple processes trying to index the same project."""
    # Create multiple indexers (simulating different processes)
    indexers = [
        CodeIndexer(
            redis_client,
            indexing_wait_time=1,
            indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
            lock_timeout=5,  # Default lock timeout is fine with active watchdog
        )
        for _ in range(3)
    ]

    # Add slow parser to each indexer
    for indexer in indexers:
        indexer.ext_to_parser[".slow"] = SlowParser

    # Track which process got the lock first
    lock_acquired = None

    # Function to attempt indexing with wait tracking
    async def try_index(indexer: CodeIndexer, order: int):
        nonlocal lock_acquired
        try:
            # Track if this process got the lock
            got_lock = False

            def log_handler(message):
                nonlocal got_lock, lock_acquired
                msg = message.record["message"]
                if "Acquired indexing lock" in msg and lock_acquired is None:
                    lock_acquired = order
                    got_lock = True

            logger.add(lambda m: log_handler(m), level="INFO")

            # First process uses overwrite=True to test temp key safety
            await indexer.build_index(
                "test-concurrent",
                [large_test_files],
                "slow",
                overwrite=(order == 0),  # First process overwrites
            )

            # Process waited if it didn't get the lock first
            waited = not got_lock
            return True, order, waited

        except Exception as e:
            if isinstance(e, RuntimeError) and (
                "Timeout waiting for indexing lock" in str(e)
                or "Project already indexed" in str(e)
            ):
                return False, order, True  # Failed processes also waited
            raise
        finally:
            logger.remove()

    # Run indexing attempts concurrently
    tasks = [
        asyncio.create_task(try_index(indexer, i)) for i, indexer in enumerate(indexers)
    ]

    results = await asyncio.gather(*tasks)

    # All processes should succeed
    success_count = sum(1 for success, _, _ in results if success)
    assert success_count == len(indexers), "All indexers should succeed"

    # Verify exactly one process got the lock first
    assert lock_acquired is not None, "One process should have acquired the lock"

    # Count processes that waited (all except the one that got lock first)
    wait_count = sum(1 for _, order, waited in results if waited)
    assert wait_count == len(indexers) - 1, "All but one indexer should have waited"

    # The process that got the lock first should not have waited
    for success, order, waited in results:
        if order == lock_acquired:
            assert not waited, "Process that got lock first should not have waited"
        else:
            assert waited, f"Process {order} should have waited"

    # Verify the index exists and is usable
    indexer = indexers[0]
    indexer.setup_project("test-concurrent")
    results = await indexer.search_function("func_1")
    assert len(results) == 10  # Should find func_1 in all 10 files


@pytest.mark.asyncio
async def test_concurrent_search_during_indexing(redis_client, large_test_files):
    """Test searching while indexing is in progress."""
    indexer1 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )
    indexer2 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )

    # Add slow parser to both indexers
    for indexer in [indexer1, indexer2]:
        indexer.ext_to_parser[".slow"] = SlowParser

    # Start indexing in background
    index_task = asyncio.create_task(
        indexer1.build_index("test-concurrent-search", [large_test_files], "slow")
    )

    # Try multiple searches while indexing is in progress
    search_results = []
    for _ in range(5):  # Try 5 times
        try:
            indexer2.setup_project("test-concurrent-search")
            results = await asyncio.wait_for(
                indexer2.search_function("func_1"), timeout=1
            )
            search_results.append(len(results))
        except asyncio.TimeoutError:
            search_results.append(None)
        await asyncio.sleep(0.5)  # Wait a bit between attempts

    # Wait for indexing to complete
    await index_task

    # Final search should succeed
    indexer2.setup_project("test-concurrent-search")
    results = await indexer2.search_function("func_1")
    assert len(results) == 10  # Should find func_1 in all 10 files

    # Verify that some searches failed (None) and the last one succeeded
    assert None in search_results, "Some searches should have timed out"
    assert search_results[-1] == 10, "Final search should have succeeded"


@pytest.mark.asyncio
async def test_concurrent_index_and_search(redis_client, large_test_files):
    """Test concurrent indexing with simultaneous search operations.

    This test simulates:
    1. Two processes trying to index the same project concurrently
    2. A third process that tries to both index and search while the others are running
    """
    # Create three indexers (simulating different processes)
    indexer1 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,
        lock_timeout=5,
    )
    indexer2 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,
        lock_timeout=5,
    )
    indexer3 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,
        lock_timeout=5,
    )

    # Add slow parser to all indexers
    for indexer in [indexer1, indexer2, indexer3]:
        indexer.ext_to_parser[".slow"] = SlowParser

    # Track which process got the lock first and waiting messages
    lock_acquired = None
    waiting_messages = []

    def log_handler(message):
        nonlocal lock_acquired, waiting_messages
        msg = message.record["message"]
        if "Acquired indexing lock" in msg and lock_acquired is None:
            if "test-concurrent-mixed" in msg:
                lock_acquired = "main"
        elif "Waiting for" in msg:
            waiting_messages.append(msg)

    logger.add(log_handler, level="DEBUG")

    # First two processes try to index the same project
    task1 = asyncio.create_task(
        indexer1.build_index("test-concurrent-mixed", [large_test_files], "slow")
    )
    task2 = asyncio.create_task(
        indexer2.build_index("test-concurrent-mixed", [large_test_files], "slow")
    )

    # Create tasks for both operations
    task3 = asyncio.create_task(
        indexer3.build_index("test-concurrent-mixed", [large_test_files], "slow")
    )

    # Third process runs indexing and searching concurrently
    async def mixed_operations():
        # Setup for searching
        indexer3.setup_project("test-concurrent-mixed")

        # Run multiple searches while indexing is happening
        try:
            # This should print waiting message since indexing is in progress
            results = await indexer3.search_function("func_1")
            search_results = ("success", len(results))
        except RuntimeError as e:
            search_results = ("error", str(e))

        return search_results

    # Run all operations concurrently
    task4 = asyncio.create_task(mixed_operations())
    results = await asyncio.gather(task1, task2, task3, task4)

    # Verify results
    search_results = results[-1]  # Results from mixed_operations

    # Verify search results
    assert (
        search_results[0] == "success"
    ), "Search should succeed while indexing was in progress"
    assert search_results[1] == 10, "Final search should find all functions"

    # Verify final search results
    indexer1.setup_project("test-concurrent-mixed")
    main_results = await indexer1.search_function("func_1")
    assert len(main_results) == 10, "Main project should be indexed correctly"

    # Verify that exactly one process got the lock for each project
    assert lock_acquired is not None, "One process should have acquired a lock"

    # Verify waiting messages
    assert len(waiting_messages) > 0, "Should have waiting messages from other indexers"
    assert any(
        "Waiting for ongoing indexing" in msg for msg in waiting_messages
    ), "Should have waiting message for indexing"
    assert any(
        "Waiting for indexing" in msg for msg in waiting_messages
    ), "Should have waiting message before search_function"

    logger.remove()


@pytest.mark.asyncio
async def test_overwrite_with_shared_prefix(redis_client, large_test_files):
    """Test that overwriting one project doesn't affect another with a shared prefix."""
    indexer1 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,
        lock_timeout=5,
    )
    indexer2 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,
        lock_timeout=5,
    )

    # Add slow parser to both indexers
    for indexer in [indexer1, indexer2]:
        indexer.ext_to_parser[".slow"] = SlowParser

    # Start indexing the first project
    task1 = asyncio.create_task(
        indexer1.build_index("test-prefix", [large_test_files], "slow")
    )

    # Wait a bit to ensure first indexing has started
    await asyncio.sleep(2)

    # Try to index a project with a shared prefix and overwrite=True
    task2 = asyncio.create_task(
        indexer2.build_index(
            "test-prefix-2", [large_test_files], "slow", overwrite=True
        )
    )

    # Wait for both tasks to complete
    await asyncio.gather(task1, task2)

    # Verify both projects were indexed correctly
    indexer1.setup_project("test-prefix")
    results1 = await indexer1.search_function("func_1")
    assert len(results1) == 10, "First project should be indexed correctly"

    indexer2.setup_project("test-prefix-2")
    results2 = await indexer2.search_function("func_1")
    assert len(results2) == 10, "Second project should be indexed correctly"


@pytest.mark.asyncio
async def test_skip_failed_file(redis_client, large_test_files):
    """Test cleanup when one indexing process fails."""
    indexer1 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )
    # Add slow parser to both indexers
    indexer1.ext_to_parser[".slow"] = SlowParser
    bad_file = large_test_files / "bad.slow"
    good_content = "\n".join(f"function content {j}" for j in range(100))
    bad_file.write_text(good_content)

    # First indexing attempt should succeed
    await indexer1.build_index(
        "skip-failed-file", [large_test_files], "slow", overwrite=True
    )

    # Verify lock was released
    assert not redis_client.exists("skip-failed-file-code-index:indexing")

    results = await indexer1.search_function("func_1")
    assert len(results) == 11  # Should find func_1 in all 11 files

    await asyncio.sleep(2)

    # Create a file that will cause parsing to fail
    bad_file.write_text("This will cause the parser to fail")

    # Second indexing attempt should skip the failed file
    await indexer1.build_index(
        "skip-failed-file", [large_test_files], "slow", overwrite=True
    )

    # Verify the index exists and is usable
    results = await indexer1.search_function("func_1")
    assert len(results) == 10  # Should find 10 func_1 in successfully indexed file
    bad_file.unlink()


@pytest.mark.asyncio
async def test_failed_indexing_cleanup(redis_client, large_test_files):
    """Test cleanup when one indexing process fails."""
    indexer1 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )
    indexer2 = CodeIndexer(
        redis_client,
        indexing_wait_time=1,
        indexing_timeout=30,  # Enough time for 10 files * 2 seconds each
        lock_timeout=5,  # Default lock timeout is fine with active watchdog
    )

    # Add slow parser to both indexers
    for indexer in [indexer1, indexer2]:
        indexer.ext_to_parser[".slow"] = SlowParser

    # First indexing attempt should fail
    with patch.object(indexer1.redis, "set", side_effect=RuntimeError("Test error")):
        with pytest.raises(RuntimeError):
            await indexer1.build_index("test-cleanup", [large_test_files], "slow")

    # Wait a bit to ensure cleanup completes
    await asyncio.sleep(2)

    # Verify lock was released
    assert not redis_client.exists("test-cleanup-code-index:indexing")

    # Second indexer should be able to acquire lock and index successfully
    await indexer2.build_index("test-cleanup", [large_test_files], "slow")

    # Verify the index exists and is usable
    indexer2.setup_project("test-cleanup")
    results = await indexer2.search_function("func_1")
    assert len(results) == 10  # Should find func_1 in all 10 files
