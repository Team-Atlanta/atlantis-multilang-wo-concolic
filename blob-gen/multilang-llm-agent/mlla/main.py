#!/usr/bin/env python3
import argparse
import asyncio
import json

# This is for submission.
import logging
import os

# import shutil
import sys
import threading
import traceback
import warnings

# from datetime import datetime
from functools import partial
from pathlib import Path
from queue import Queue
from typing import Any, Optional

import tokencost
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from libCRS.otel import install_otel_logger
from loguru import logger
from typing_extensions import Annotated, Dict, List, TypedDict

from .agents.bcda_experimental import BugCandDetectAgent
from .agents.bugcandidate_agent.path_extractor import (
    ExtractedPath,
    extract_unexplored_paths,
    path_consumer,
)
from .agents.cpua import CPUnderstandAgent

# from .agents.bga import BlobGenAgent
# from .agents.bga_refactored import BlobGenAgent
from .agents.orchestrator_agent.agent import OrchestratorAgent
from .agents.sanua import SanUnderstandAgent

# TODO: Need to figure out sanitizers to check pov
# from .scripts.test_bga_checker_result import gen_sanitizer_result
from .utils.agent import (
    BCDA,
    BLOBGEN_AGENT,
    CGPA,
    CPUA,
    EA,
    GENERATOR_AGENT,
    MCGA,
    MUTATOR_AGENT,
    ORCHESTRATOR_AGENT,
    SANUA,
)
from .utils.bit import BugInducingThing
from .utils.cg import CG
from .utils.context import GlobalContext
from .utils.display_metrics import display_agent_metrics
from .utils.run_pov import run_pov_and_check
from .utils.signal_handler import setup_signal_handler
from .utils.state import merge_with_update
from .utils.telemetry import setup_telemetry

# from .agents.ea import ExecuteAgent
# from .agents.sanua import SanUnderstandAgent


# logging.basicConfig(level=logging.INFO)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


# warnings.simplefilter("ignore", UserWarning)
warnings.filterwarnings(
    "ignore", message="Streaming with Pydantic response_format not yet supported."
)


class InputState(TypedDict):
    cp_path: Path


class IntermediateState(TypedDict):
    sink_functions: list
    # Input of bcda
    extracted_paths: List[ExtractedPath]
    # Input of blob_gen
    CGs: Annotated[Dict[str, List[CG]], merge_with_update]


class OutputState(TypedDict):
    blobs: Dict[str, List[bytes]]


class OverallState(InputState, IntermediateState, OutputState):
    # lang: str
    # source_code: str
    # commits: list
    BITs: List[BugInducingThing]


def preprocess(gc: GlobalContext, state: InputState) -> OverallState:
    if gc.no_llm:
        out_state = gc.get_state()
        return out_state

    cp_path = state["cp_path"]

    # TODO: generate it from san_understand
    sink_functions: List[str] = []

    return {
        "cp_path": cp_path,
        "BITs": [],
        "sink_functions": sink_functions,
        "CGs": {},
        "blobs": {},
        "extracted_paths": [],
    }


def supernode_bcda_bga(gc: GlobalContext):
    BCDA = BugCandDetectAgent(gc)
    OA = OrchestratorAgent(gc)
    builder = StateGraph(OverallState, input=IntermediateState, output=OutputState)
    builder.add_node("bcda", BCDA.compile())
    builder.add_node("bga", OA.compile())
    builder.add_edge(START, "bcda")
    # What if no BIT? BGA with no BIT should be run?
    builder.add_conditional_edges(
        "bcda",
        lambda state: "has_bits" if state["BITs"] else "no_bits",
        {
            "has_bits": "bga",
            "no_bits": END,
        },
    )
    builder.add_edge("bga", END)

    return builder.compile()


def main_graph(gc: GlobalContext):

    CPUA = CPUnderstandAgent(gc)
    # BCDA = BugCandDetectAgent(gc)
    # OA = OrchestratorAgent(gc)
    # EA = ExecuteAgent()

    builder = StateGraph(OverallState, input=InputState, output=IntermediateState)
    builder.add_node("preprocess", partial(preprocess, gc))
    builder.add_node("cp_understand", CPUA.compile())
    builder.add_node("path_extractor", partial(extract_unexplored_paths, gc))
    # builder.add_node("bug_candidate_detect", BCDA.compile())
    # builder.add_node("blob_gen", OA.compile())
    # builder.add_node("execute", EA.compile())

    builder.add_edge(START, "preprocess")
    builder.add_edge("preprocess", "cp_understand")
    builder.add_edge("cp_understand", END)
    # builder.add_edge("cp_understand", "path_extractor")
    # builder.add_edge("path_extractor", END)
    # builder.add_edge("bug_candidate_detect", "blob_gen")
    # builder.add_edge("blob_gen", END)
    # builder.add_edge("blob_gen", "execute")

    graph = builder.compile(checkpointer=gc.checkpointer)

    return graph


async def call_generator_agent_standalone(
    gc: GlobalContext, run_sanitizer_selection=False, diff_path="/src/ref.diff"
):
    """Create a graph that only runs the BGA (Blob Generation Agent)."""
    import signal

    generator_timeout = int(os.environ.get("BGA_GENERATOR_STANDALONE_TIMEOUT", "1000"))

    def timeout_handler(signum, frame):
        logger.warning(f"Timeout!!! After {generator_timeout} seconds")
        os.kill(os.getpid(), signal.SIGTERM)
        raise KeyboardInterrupt

    if generator_timeout > 0:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(generator_timeout)

    # Get number of test runs from environment variable
    num_tests = int(os.environ.get("BGA_GENERATOR_STANDALONE_EVAL_NUM_TEST", "1"))

    logger.info(
        f"Running standalone mode generator {num_tests} time(s) w/ timeout:"
        f" {generator_timeout} seconds"
    )

    from .agents.generator_agent.agent import GeneratorAgent, GeneratorAgentInputState

    graph = GeneratorAgent(gc).compile()

    # Determine sanitizer based on language
    if gc.cp.language == "jvm":
        sanitizer = "jazzer"
    else:
        sanitizer = gc.cp.sanitizers[0]

    harness_name = gc.target_harness
    harness = gc.cp.harnesses[harness_name]
    harness_path: Path = harness.src_path

    # Prepare generator agent input state
    generator_input = GeneratorAgentInputState(
        standalone=True,
        harness_name=harness_name,
        source_path=str(harness_path),
        diff_path=diff_path,
        run_sanitizer_selection=run_sanitizer_selection,
        sanitizer=sanitizer,
    )

    # Run generator agent multiple times independently and asynchronously
    logger.info(f"Starting {num_tests} independent generator runs concurrently")

    async def run_single_generator(run_id: int):
        """Run a single generator instance."""
        logger.info(f"Starting independent generator run {run_id+1}/{num_tests}")
        try:
            # Each run uses the same initial input - completely independent
            final_state = await graph.ainvoke(generator_input, {"recursion_limit": 100})
            logger.info(f"Completed generator run {run_id+1}/{num_tests}")
            return final_state
        except Exception as e:
            logger.error(f"Generator run {run_id+1}/{num_tests} failed: {e}")
            return None

    # Create tasks for all runs and execute them concurrently
    tasks = [run_single_generator(i) for i in range(num_tests)]
    final_states = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle any exceptions that were returned
    processed_states = []
    for i, state in enumerate(final_states):
        if isinstance(state, Exception):
            logger.error(f"Generator run {i+1}/{num_tests} raised exception: {state}")
            # processed_states.append(None)
        else:
            processed_states.append(state)

    final_state = processed_states[-1] if processed_states else {}

    logger.info(
        f"Selected final state from {len(processed_states)} successful runs out of"
        f" {num_tests} total runs"
    )

    gc.finalize()

    # Run POV checks on saved blobs
    logger.debug("Running POV checks on saved blobs")
    await run_pov_and_check(gc)

    return final_state


def test_graph(gc: GlobalContext):
    memory = MemorySaver()
    CPUA = CPUnderstandAgent(gc)
    # SanUA = SanUnderstandAgent()
    # BGA = BlobGenAgent(gc)
    OA = OrchestratorAgent(gc)
    BCDA = BugCandDetectAgent(gc)
    # EA = ExecuteAgent()

    builder = StateGraph(OverallState)
    builder.add_node("cp_understand", CPUA.compile())
    builder.add_node("blob_gen", OA.compile())
    builder.add_node("bug_candidate_detect", BCDA.compile())

    builder.add_edge(START, "cp_understand")
    builder.add_edge("cp_understand", "bug_candidate_detect")
    builder.add_edge("bug_candidate_detect", "blob_gen")
    builder.add_edge("blob_gen", END)

    graph = builder.compile(checkpointer=memory)

    return graph


def basic_test_graph(gc: GlobalContext):
    def _test_edge(state: OverallState):
        state["blobs"] = {"harness2": [b"blob3", b"blob4"]}
        return "test_node"

    def _test_node(state: OverallState):
        logger.debug(state)
        return state

    memory = MemorySaver()
    builder = StateGraph(OverallState)
    builder.add_node("test_node", _test_node)

    builder.add_conditional_edges(START, _test_edge)
    builder.add_edge("test_node", END)

    graph = builder.compile(checkpointer=memory)

    return graph


def init(args) -> GlobalContext:
    cp_path = Path(args.cp).resolve()
    agent_names = []
    if args.cmd and args.cmd.startswith("load"):
        agent_names = args.cmd.split(",")[1:]
        if len(agent_names) == 0:
            agent_names = [BCDA, CPUA, EA, MCGA, SANUA]

    gc = GlobalContext(
        no_llm=args.no_llm,
        cp_path=cp_path,
        load_agent_names=agent_names,
        target_harness=args.harness,
        workdir=args.workdir,
        output_dir=args.output,
        redis_host=args.redis,
        in_ci=args.ci,
        in_eval=args.eval,
        soyeon_debug=args.soyeon_debug,
        standalone=args.agent is not None,
    )

    # Always assume that we are running inside docker
    # if not is_running_in_docker():
    #     logger.error("Running MLLA outside of docker is disabled!")
    #     raise NotImplementedError
    #     # # Lets start building environments
    #     # logger.info("Building necessary docker images...")
    #     # try:
    #     #     # Build fuzzers for each harness
    #     #     for harness_name in gc.cp.harnesses:
    #     #         logger.info(f"Building fuzzer for harness: {harness_name}")
    #     #         build_docker_images(gc, harness_name, None)
    #     # except Exception as e:
    #     #     logger.error(f"Failed to build docker images: {e}")
    #     #     sys.exit(1)

    return gc


async def finalize(gc: GlobalContext, final_state):
    """Finalize the execution by saving blobs and running POV checks."""
    gc.finalize()

    # # Get blobs from final state
    # blobs = final_state.get("blobs", {})
    # if not blobs:
    #     logger.warning("No blobs found in final state")
    #     return

    # Run POV checks on saved blobs
    logger.debug("Running POV checks on saved blobs")
    await run_pov_and_check(gc)

    # DK: Temperarily disable this.
    # # Cleanup old directories, keeping the 10 most recent
    # logger.info("Cleaning up old blob directories")
    # do_not_delete_blob_dirs = sorted(
    #     gc.BLOBS_DIR.iterdir(), key=lambda x: x.name, reverse=True
    # )[:10]

    # for old_dir in gc.BLOBS_DIR.iterdir():
    #     if old_dir not in do_not_delete_blob_dirs:
    #         try:
    #             shutil.rmtree(old_dir)
    #             logger.debug(f"Removed old blob directory: {old_dir}")
    #         except Exception as e:
    #             logger.error(f"Failed to remove old blob directory {old_dir}: {e}")


def remove_incomplete_caches(gc: GlobalContext):
    from mlla.agents.bugcandidate_agent.path_extractor import reset_incompletes

    reset_incompletes(gc)


def draw_graph(gc: GlobalContext, agent_name: str, main_graph=None) -> None:
    """Draw a graph visualization for a specific agent or all agents."""
    output_file = f"images/overview_{agent_name}.png"

    if agent_name == "all":
        # Draw the main graph with all agents
        image = main_graph.get_graph(xray=2).draw_mermaid_png()
    else:
        # Draw graph for specific agent
        builder = StateGraph(OverallState, input=InputState, output=OutputState)
        agent_map = {
            "bga": OrchestratorAgent(gc),
            "cpua": CPUnderstandAgent(gc),
            "sanua": SanUnderstandAgent(gc),
            "bcda": BugCandDetectAgent(gc),
        }

        if agent_name not in agent_map:
            logger.error(
                f"Unknown agent: {agent_name}. Available agents:"
                f" {', '.join(agent_map.keys())}"
            )
            return

        agent = agent_map[agent_name]
        builder.add_node(agent_name, agent.compile())
        builder.add_edge(START, agent_name)
        builder.add_edge(agent_name, END)
        graph = builder.compile()
        image = graph.get_graph(xray=2).draw_mermaid_png()

    # Save the graph visualization
    with open(output_file, "wb") as f:
        f.write(image)
    logger.info(f"Graph visualization saved to {output_file}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "LLM Agent to find bugs and generate input blobs for"
            " applications written in multiple languages."
        )
    )
    parser.add_argument(
        "--cp", type=str, required=True, help="Path to the CP directory."
    )

    parser.add_argument(
        "--dev",
        action="store_true",
        help="Run the agent in development mode.",
    )

    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run the agent without connecting to the LLM API.",
    )

    parser.add_argument(
        "--lg-debug",
        action="store_true",
        help="Print LangGraph states when running.",
    )

    parser.add_argument(
        "--cmd",
        type=str,
        required=False,
        help="Command to run the agent.",
    )

    parser.add_argument(
        "--crs-multilang-path",
        type=str,
        help="Path to CRS-multilang repository.If not provided, will try to detect it.",
    )

    parser.add_argument(
        "--harness",
        type=str,
        required=True,
        help="Harness name to run the agent for.",
    )

    parser.add_argument(
        "--workdir",
        type=str,
        default="results",
        help="Directory to store intermediate results (default: results)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Directory to store generated blobs (default: {workdir}/blobs)",
    )

    parser.add_argument(
        "--redis",
        type=str,
        help="Redis server address (default: localhost or docker gateway)",
    )

    parser.add_argument(
        "--draw",
        type=str,
        default=None,
        help=(
            "Generate graph visualization for specific agent "
            "(all, bga, cpua, sanua, bcda) and exit. "
            "The file name will be 'overview_{agent_name}.png'"
        ),
    )

    # Telemetry configuration
    standalone_group = parser.add_argument_group("Agent standalone")
    standalone_group.add_argument(
        "--agent",
        type=str,
        choices=["generator"],  # For now, only bga is supported
        help="Run only the specified agent",
    )
    standalone_group.add_argument(
        "--run-sanitizer-selection",
        action="store_true",
        help="Run select sanitizer for generator agent",
        default=False,
    )
    standalone_group.add_argument(
        "--diff-path",
        type=str,
        help="Run generator with diff file",
        default="/src/ref.diff",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )

    parser.add_argument(
        "--ci",
        action="store_true",
        help="Run the agent in CI mode",
        default=False,
    )

    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run the agent in eval mode",
        default=False,
    )

    parser.add_argument(
        "--soyeon-debug",
        action="store_true",
        help="Run the agent in soyeon debug mode",
        default=False,
    )

    # Telemetry configuration
    telemetry_group = parser.add_argument_group("Telemetry Options")
    telemetry_group.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Enable telemetry for LLM operations",
    )
    telemetry_group.add_argument(
        "--telemetry-endpoint",
        type=str,
        default=None,
        help="OpenTelemetry collector endpoint",
    )
    telemetry_group.add_argument(
        "--project-name",
        type=str,
        default="default",
        help="Project name for telemetry",
    )
    telemetry_group.add_argument(
        "--telemetry-provider",
        type=str,
        choices=["phoenix", "traceloop"],
        default="phoenix",
        help="Telemetry provider to use (phoenix or traceloop)",
    )

    args = parser.parse_args()
    return args


async def main() -> None:
    try:
        json_path = (
            Path(os.path.dirname(__file__))
            / "assets"
            / "model_prices_and_context_window.json"
        )
        if json_path.exists():
            data = json.load(open(json_path))
            tokencost.TOKEN_COSTS.update(data)
    except Exception as e:
        logger.warning(f"Error updating token costs: {e}")

    candidate_queue: Queue[Optional[dict]] = Queue()
    thread: threading.Thread | None = None

    try:
        from datetime import datetime

        start_time = datetime.now()
        args = _parse_args()

        logger.remove()
        logger.add(sys.stderr, level=args.log_level)

        # Create temporary in-memory buffer to capture early logs
        from io import StringIO

        log_buffer = StringIO()
        buffer_handler_id = logger.add(log_buffer, level=args.log_level)

        install_otel_logger(action_name="mlla")

        # Initialize telemetry if enabled
        if args.enable_telemetry:
            try:
                setup_telemetry(
                    project_name=args.project_name,
                    endpoint=args.telemetry_endpoint,
                    provider=args.telemetry_provider,
                )
            except SystemExit as e:
                logger.error("Failed to initialize telemetry. Exiting.")
                sys.exit(e.code)

        gc = init(args)
        if not gc.cp.harnesses:
            logger.warning(f"This is no harnesses in the target {gc.cp.name}")
            return

        # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # log_file = f"logs/{gc.cp.name}_{gc.cur_harness.name}_{timestamp}.log"
        # logger.info(f"Logging to {log_file}")
        # os.makedirs("logs", exist_ok=True)
        # logger.add(log_file, level=args.log_level)

        logger.info(f"Logging to {gc.LOG_FILE}")

        # Remove the temporary buffer handler
        logger.remove(buffer_handler_id)

        # Write buffered logs to the final log file
        buffered_content = log_buffer.getvalue()
        if buffered_content:
            with open(gc.LOG_FILE, "w") as f:
                f.write(buffered_content)

        # Add the final log file handler
        logger.add(str(gc.LOG_FILE), level=args.log_level)

        # Set the process group for this process and its children
        # If we set this, this will block KeyboardInterrupt.
        # os.setpgrp()
        setup_signal_handler(gc)

        # TODO: Need to figure out sanitizers to check pov
        # gen_sanitizer_result(gc.cp)

        if args.cmd == "run_pov":
            # This will automatically search latest blob dir.
            await run_pov_and_check(gc)
            return

        # Select which graph to use based on --agent flag
        if args.agent == "generator":
            final_state = await call_generator_agent_standalone(
                gc,
                run_sanitizer_selection=args.run_sanitizer_selection,
                diff_path=args.diff_path,
            )

        else:
            graph = main_graph(gc)
            # bcda = BugCandDetectAgent(gc).compile()
            # bga = OrchestratorAgent(gc).compile()

            if args.draw:
                draw_graph(gc, args.draw, graph)
                return

            if args.no_llm:
                gc.register_history_tracker(graph)

            remove_incomplete_caches(gc)

            gc.set_candidate_queue(candidate_queue)
            num_workers = 6
            logger.info(f"Running with {num_workers} workers")

            async def _execute_cpua(candidate_queue: Queue):
                cpua_future = asyncio.ensure_future(
                    graph.ainvoke(
                        {
                            "cp_path": gc.cp.proj_path,
                        },
                        gc.graph_config,
                        debug=args.lg_debug,
                    )
                )

                def _add_sentinel_to_workers(candidate_queue: Queue):
                    for idx in range(num_workers):
                        logger.info(f"Adding sentinel to Consumer {idx}")
                        candidate_queue.put(None)

                cpua_future.add_done_callback(
                    lambda f: _add_sentinel_to_workers(candidate_queue)
                )
                return await cpua_future

            def run_bcda_bga_in_thread():
                async def thread_main():

                    bcda_bga_graph = supernode_bcda_bga(gc)
                    consumer_tasks = [
                        asyncio.create_task(
                            path_consumer(
                                gc, candidate_queue, bcda_bga_graph, worker_id=i
                            )
                        )
                        for i in range(num_workers)
                    ]
                    results = await asyncio.gather(
                        *consumer_tasks, return_exceptions=True
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"Task failed: {result}")
                            tb_lines = traceback.format_exception(
                                type(result),
                                result,
                                result.__traceback__,
                                chain=True,
                                limit=10,
                            )
                            logger.error("".join(tb_lines))

                asyncio.run(thread_main())

            async with gc.init():
                logger.info(
                    f"Checking initialization time: {datetime.now() - start_time}"
                )

                thread = threading.Thread(target=run_bcda_bga_in_thread)
                thread.start()
                await _execute_cpua(candidate_queue)
                logger.info("CPUA done")

                # tasks = []
                # for path in intermediate_state["extracted_paths"]:
                #     supernode_input = deepcopy(intermediate_state)
                #     supernode_input["extracted_paths"] = [path]

                #     task = asyncio.create_task(bcda_bga_graph
                # .ainvoke(supernode_input))
                #     tasks.append(task)

                # for completed in asyncio.as_completed(tasks):
                #     try:
                #         result = await completed

                #         if result is not None:
                #             logger.info(f"BGA results: {result}")
                #     except Exception as e:
                #         logger.error(f"Task failed: {e}")
                #         tb_lines = traceback.format_exception(
                #             type(e), e, e.__traceback__, chain=True, limit=10
                #         )
                #         logger.error("".join(tb_lines))

    except Exception as e:
        logger.error(f"Error: {e}")
        tb_lines = traceback.format_exception(
            type(e), e, e.__traceback__, chain=True, limit=10
        )
        logger.error("".join(tb_lines))
        raise e
    finally:
        final_state = None  # final_state is not used anywhere

        candidate_queue.join()
        logger.info("Candidate queue joined")
        if thread:
            thread.join()
        logger.info("[MAIN] All tasks done")
        await finalize(gc, final_state)
        # DO NOT REMOVE THIS.
        # ref_diff = Path("/tarballs/ref.diff")
        # if ref_diff.exists():
        #     logger.info("Removing ref.diff...")
        #     ref_diff.unlink()

        done_cnt_file = Path(gc.workdir) / gc.cp.name / f"{gc.cur_harness.name}.done"
        done_cnt_file.parent.mkdir(parents=True, exist_ok=True)
        if done_cnt_file.exists():
            try:
                with open(done_cnt_file, "r") as f:
                    cnt = int(f.read())
                with open(done_cnt_file, "w+") as f:
                    cnt += 1
                    f.write(str(cnt))
            except Exception as e:
                logger.error(f"Error writing done count: {e}")

                with open(done_cnt_file, "w") as f:
                    f.write("1")
                cnt = 1

                logger.info("Flushing MCGA cache...")
                mcga_keys: Any = gc.redis.keys(
                    f"mcga::{gc.cp.name}::{gc.cur_harness.name}::*"
                )
                for key in mcga_keys:
                    gc.redis.delete(key)

            logger.info(f"MLLA ran {cnt} times for {gc.cp.name}_{gc.cur_harness.name}")
            if cnt % 3 == 0:
                logger.info("Flushing MCGA cache...")
                _mcga_keys: Any = gc.redis.keys(
                    f"mcga::{gc.cp.name}::{gc.cur_harness.name}::*"
                )
                for key in _mcga_keys:
                    gc.redis.delete(key)

        else:
            logger.info(f"MLLA ran 1 time for {gc.cp.name}_{gc.cur_harness.name}")
            with open(done_cnt_file, "w") as f:
                f.write("1")

        # Print comprehensive metrics to logger
        logger.info("============== LLM Usage and Agent Metrics ====================")
        display_agent_metrics(
            gc,
            [
                SANUA,
                CPUA,
                MCGA,
                BCDA,
                CGPA,
                ORCHESTRATOR_AGENT,
                MUTATOR_AGENT,
                GENERATOR_AGENT,
                BLOBGEN_AGENT,
            ],
            logger.info,
        )
        logger.info("==============================================================")

        # ==== below is for CI
        # Print comprehensive metrics to stdout
        print("============== LLM Usage and Agent Metrics ====================")
        display_agent_metrics(
            gc,
            [
                SANUA,
                CPUA,
                MCGA,
                BCDA,
                CGPA,
                ORCHESTRATOR_AGENT,
                MUTATOR_AGENT,
                GENERATOR_AGENT,
                BLOBGEN_AGENT,
            ],
            print,
            include_per_model=False,
        )
        print("==============================================================")


if __name__ == "__main__":
    asyncio.run(main())
