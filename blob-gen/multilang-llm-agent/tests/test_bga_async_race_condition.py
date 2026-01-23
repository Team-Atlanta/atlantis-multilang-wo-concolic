import asyncio
from unittest.mock import MagicMock

import pytest

from mlla.agents.orchestrator_agent.modules import run_all_agents
from mlla.utils.cp import sCP
from tests.dummy_context import DummyContext


async def mock_async_run_input(*args, **kwargs):
    # Simulate the real behavior where CP_Harness manages its own loop
    if (
        not hasattr(mock_async_run_input, "_loop")
        or mock_async_run_input._loop.is_closed()
    ):
        mock_async_run_input._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(mock_async_run_input._loop)

    await asyncio.sleep(0.1)  # Simulate some async work
    return (b"stdout", b"stderr", b"{}", b"crash_log")


@pytest.mark.asyncio
@pytest.mark.skip(reason="This test is deprecated.")
async def test_race_condition():
    """Test that bga_generator can run while other async operations are happening."""
    # Mock the necessary components
    mock_cp = MagicMock(spec=sCP)
    mock_cp.language = "c"
    mock_cp.harnesses = {"test_harness": MagicMock()}
    mock_cp.harnesses["test_harness"].async_run_input = mock_async_run_input

    # Create a DummyContext with the mock CP
    gc = DummyContext(no_llm=False)
    gc._cp = mock_cp

    # Create a state that will trigger multiple async operations
    state = {
        "blobgen_contexts": [
            {
                "harness_name": "test_harness",
                "sanitizer": "address",
                "cg_name": f"cg_{i}",
                "attr_cg": MagicMock(),
                "selected_sanitizers": ["address"],
                "payload_dict": {},
                "current_retry": 0,
                "context": "",
                "error": "",
                "error_context": {},
                "start_time": None,
            }
            for i in range(5)  # Create 5 contexts to process
        ],
        "standalone": False,
        "sanitizer": "address",
    }

    # Start some async operations in a separate thread
    async def run_async_operations():
        # Simulate other async operations happening
        tasks = []
        for i in range(3):

            async def async_work():
                await asyncio.sleep(0.1)  # Simulate some async work
                return f"Result {i}"

            tasks.append(asyncio.create_task(async_work()))

        results = await asyncio.gather(*tasks)
        return results

    async_results = []
    async_error = None

    async_task = asyncio.create_task(run_async_operations())

    # Now call blobgen_agent normally
    try:
        result = await run_all_agents(state)
        assert isinstance(result, dict), "blobgen agent should return a dict"
        # assert "payload_dict" in result, "result should have payload_blobs"
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            pytest.fail("blobgen agent failed with 'Event loop is closed' error")
        raise

    async_results = await async_task

    if async_error:
        raise async_error

    # Verify async operations completed successfully
    assert len(async_results) == 3, "Should have 3 async results"
    assert all(
        isinstance(r, str) for r in async_results
    ), "All results should be strings"


@pytest.mark.asyncio
async def test_concurrent_pov_runs():
    # Similar setup as above
    mock_cp = MagicMock(spec=sCP)
    mock_cp.language = "c"
    mock_cp.harnesses = {"test_harness": MagicMock()}
    mock_cp.harnesses["test_harness"].async_run_input = mock_async_run_input

    # Create a DummyContext with the mock CP
    gc = DummyContext(no_llm=False)
    gc._cp = mock_cp

    # Create a timeout for the entire test
    async def run_with_timeout():
        # Create multiple POV runs that will compete for the event loop
        async def run_concurrent_povs():
            from mlla.utils.run_pov import process_pov_in_docker_async

            # Create multiple POV runs
            tasks = []
            for i in range(5):
                task = asyncio.create_task(
                    process_pov_in_docker_async(
                        gc, "test_harness", f"hash_{i}", b"test_blob", i + 1, 5
                    )
                )
                tasks.append(task)

            # Force event loop issues while POVs are running
            await asyncio.sleep(0.05)

            # Get the current loop
            loop = asyncio.get_event_loop()

            # Create new loop and set it
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)

            # Try to close the old loop
            try:
                loop.close()
            except Exception as e:
                print(f"Expected error closing loop: {e}")

            # Wait for all POV runs to complete with a timeout
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=5.0
                )
                return results
            except asyncio.TimeoutError:
                # Cancel any remaining tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()
                raise
            finally:
                # Ensure all tasks are cleaned up
                for task in tasks:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

        # Run the POV operations with a timeout
        try:
            return await asyncio.wait_for(run_concurrent_povs(), timeout=10.0)
        except asyncio.TimeoutError:
            pytest.fail("Test timed out")

    # Run the test with timeout
    results = await run_with_timeout()

    # Check for event loop errors
    event_loop_errors = [
        r
        for r in results
        if isinstance(r, RuntimeError) and "Event loop is closed" in str(r)
    ]
    assert len(event_loop_errors) == 0, "Found Event loop is closed errors"
