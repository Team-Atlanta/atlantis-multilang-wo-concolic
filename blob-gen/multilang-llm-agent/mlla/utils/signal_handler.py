import os
import signal
import sys
import traceback
from functools import partial

import psutil
from loguru import logger
from typing_extensions import Dict, List

from .agent import BCDA, CPUA, ORCHESTRATOR_AGENT
from .context import GlobalContext
from .display_metrics import display_agent_metrics


def setup_signal_handler(gc: GlobalContext):
    # Create signal handler with GlobalContext
    custom_handler = partial(signal_handler, gc)

    # Register handlers for all termination signals
    signal.signal(signal.SIGTERM, custom_handler)  # timeout command
    signal.signal(signal.SIGINT, custom_handler)  # Ctrl+C
    signal.signal(signal.SIGQUIT, custom_handler)  # Ctrl+\
    signal.signal(signal.SIGHUP, custom_handler)  # Terminal disconnect


def signal_handler(gc: GlobalContext, signum, frame) -> None:
    """Handle termination signals by cleaning up all processes."""

    signal_names = {
        signal.SIGTERM: "SIGTERM",
        signal.SIGINT: "SIGINT",
        signal.SIGQUIT: "SIGQUIT",
        signal.SIGHUP: "SIGHUP",
    }

    # Print backtrace
    logger.info("============== Backtrace ====================")
    traceback.print_stack(frame)
    logger.info("============================================")

    signal_name = signal_names.get(signum, str(signum))
    logger.warning(f"Received {signal_name}, terminating all processes...")

    # First terminate any active harness runners
    for harness in gc._cp.harnesses.values():
        if hasattr(harness, "runner") and harness.runner:
            try:
                logger.info(f"Terminating harness runner for {harness.name}")
                kill_process_tree(harness.runner.pid)
            except Exception as e:
                logger.error(f"Error terminating harness runner: {e}")

    # Print comprehensive metrics
    logger.info("============== LLM Usage and Agent Metrics ====================")
    display_agent_metrics(gc, [CPUA, BCDA, ORCHESTRATOR_AGENT], logger.info)
    logger.info("==============================================================")

    # Finally terminate all processes in the group
    try:
        pgid = os.getpgid(0)
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass  # Process group already dead or we don't have permission

    sys.exit(0)


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants by process group."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)

        # Include the parent in the group list too
        all_procs = children + [parent]

        groups: Dict[int, List[psutil.Process]] = {}
        for proc in all_procs:
            try:
                pgid = os.getpgid(proc.pid)
                groups.setdefault(pgid, []).append(proc)
            except Exception:
                continue

        for pgid, procs in groups.items():
            logger.warning(f"Sending SIGKILL to process group {pgid}")
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                for p in procs:
                    try:
                        logger.warning(f"Killing PID {p.pid} ({p.name()})")
                        p.kill()
                    except Exception:
                        continue

    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
