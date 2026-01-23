import asyncio

# import time
from typing import Any, Coroutine

# from tqdm import tqdm


async def run_with_timeout_bar(
    coro: Coroutine, timeout: float, desc: str = "Timeout Progress"
) -> Any:
    """
    Run an async coroutine with a tqdm-based timeout progress bar.

    Args:
        coro: The coroutine to run.
        timeout: Timeout duration in seconds.
        desc: Description for the progress bar.

    Returns:
        The result of the coroutine if it completes in time.

    Raises:
        asyncio.TimeoutError: If the coroutine doesn't finish within the timeout.
    """

    # async def _timeout_bar():
    #     with tqdm(total=timeout, desc=desc, unit="s", dynamic_ncols=True) as pbar:
    #         start = time.time()
    #         while True:
    #             elapsed = time.time() - start
    #             if elapsed >= timeout:
    #                 break
    #             pbar.n = int(elapsed)
    #             pbar.refresh()
    #             await asyncio.sleep(1)
    #         pbar.n = timeout
    #         pbar.refresh()

    # bar_task = asyncio.create_task(_timeout_bar())

    # try:
    result = await asyncio.wait_for(coro, timeout=timeout)
    return result
    # finally:
    #     bar_task.cancel()
    #     try:
    #         await bar_task
    #     except asyncio.CancelledError:
    #         pass
