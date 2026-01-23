import asyncio
import os
import signal
import time
from functools import partial

from .main import _parse_args, finalize, init, main_graph
from .utils.signal_handler import signal_handler


async def test() -> None:
    try:
        os.setpgrp()
        args = _parse_args()
        gc = init(args)
        custom_handler = partial(signal_handler, gc.general_callback)
        # Register the Ctrl-C signal handler
        signal.signal(signal.SIGINT, custom_handler)
        graph = main_graph(gc)
        final_state = graph.invoke(
            {
                "cp_path": gc.cp.proj_path,
            },
            gc.graph_config,
            debug=args.lg_debug,
        )
        await finalize(gc, final_state)

        while True:
            print("hello")
            time.sleep(1)
    except Exception as e:
        raise e
    finally:
        print("world")


if __name__ == "__main__":
    asyncio.run(test())
