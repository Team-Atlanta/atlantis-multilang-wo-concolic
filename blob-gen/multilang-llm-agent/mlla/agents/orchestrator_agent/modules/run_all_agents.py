"""OrchestratorAgent that coordinates BlobGenAgent, GeneratorAgent, and MutatorAgent."""

import asyncio
import os
import traceback
from typing import Any, Dict, List, Set

from loguru import logger

from ....modules.sanitizer import get_sanitizer_classname, is_known_crash
from ....utils.attribute_cg import AttributeCG, find_matching_bit
from ....utils.bit import BugInducingThing
from ....utils.context import GlobalContext
from ....utils.coverage import (
    is_path_already_crashed,
    load_found_bits,
    load_previous_crashes,
)
from ...orchestrator_agent.state import BlobGenContext
from ..state import OrchestratorAgentOverallState
from .call_blobgen_agent import process_blobgen_from_contexts
from .call_generator_agent import process_generator_from_contexts
from .call_mutator_agent import process_mutator_from_contexts


def create_blobgen_contexts(
    gc: GlobalContext,
    CGs: Dict[str, List[Any]],
    BITs: List[BugInducingThing],
    sanitizer: str,
) -> List[BlobGenContext]:
    """Create contexts for BlobGenAgent processing."""
    NUM_TEST = int(os.getenv("ORCHESTRATOR_EVAL_NUM_TEST", "1"))
    blobgen_contexts = []

    # Get harness information
    harness = gc.cur_harness
    harness_name = harness.name

    # Load previously found crashes for filtering
    previous_crashes = load_previous_crashes(gc.CRASH_DIR, harness_name, sanitizer)
    if previous_crashes:
        logger.info(
            f"Loaded {len(previous_crashes)} previously crashed paths to filter out"
        )

    # Load previously found BITs for filtering
    found_bits = load_found_bits(gc.workdir)

    # Process BITs based on priority
    prioritized_bits = _process_bits_by_priority(BITs)

    # Process BITs and CGs
    visited_cg: Set = set()

    try:
        # First, process all BITs against CGs
        for bit in prioritized_bits:
            # # =====================================================
            # # FOR DEBUG
            # sanitizer_type = bit.analysis_message[0].sanitizer_type
            # if "OsCommandInjection" not in sanitizer_type:
            #     continue
            # # =====================================================

            bit_contexts = _process_bit_for_cgs(
                bit,
                harness,
                harness_name,
                sanitizer,
                previous_crashes,
                visited_cg,
                NUM_TEST,
                found_bits,
                CGs,
                gc,
            )
            blobgen_contexts.extend(bit_contexts)

        # Then, process any unvisited CGs
        handle_unvisited = os.getenv("ORCHESTRATOR_HANDLE_UNVISITED_CGS", False)
        if handle_unvisited:
            unvisited_contexts = _process_unvisited_cgs(
                harness_name,
                sanitizer,
                previous_crashes,
                visited_cg,
                CGs,
                gc,
            )
            blobgen_contexts.extend(unvisited_contexts)

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Error processing context creation: {error_msg}")

    return blobgen_contexts


def extract_sanitizers_from_bit(
    bit: BugInducingThing, base_sanitizer: str
) -> List[str]:
    """Extract sanitizer types from a BIT."""
    if not bit or not bit.analysis_message:
        return []

    selected_sanitizer_set = set()
    for message in bit.analysis_message:
        # Split sanitizer type and add each part separately
        for sanitizer_type in message.sanitizer_type.split(","):
            sanitizer_type = sanitizer_type.strip()
            # If it's a continuation of previous type (no sanitizer prefix)
            if base_sanitizer not in sanitizer_type:
                sanitizer_name = get_sanitizer_classname(base_sanitizer)
                sanitizer_type = f"{sanitizer_name}.{sanitizer_type}"

            if is_known_crash(sanitizer_type):
                selected_sanitizer_set.add(sanitizer_type)

    return list(selected_sanitizer_set)


def _process_bits_by_priority(BITs: List[BugInducingThing]) -> List[BugInducingThing]:
    """Process BITs based on priority, duplicating higher priority BITs."""
    original_bits = BITs
    prioritized_bits = []

    # Process BITs based on priority
    for bit in original_bits:
        # Add the original BIT
        prioritized_bits.append(bit)

        # If priority is higher than NORMAL (1), duplicate the BIT
        if bit.priority > 1:
            # Add additional copies (priority - 1 times)
            for _ in range(bit.priority - 1):
                prioritized_bits.append(bit)
            logger.debug(
                f"Duplicated BIT {bit.func_location.func_name} {bit.priority} times "
                f"due to priority level {bit.priority}"
            )

    if len(prioritized_bits) > len(original_bits):
        logger.info(
            f"Increased BITs from {len(original_bits)} to {len(prioritized_bits)} "
            "based on priority levels"
        )

    return prioritized_bits


def _create_blobgen_context(
    harness_name: str, sanitizer: str, cg, attr_cg: AttributeCG, num_tests: int = 1
) -> List[BlobGenContext]:
    """Create contexts with a matching BIT."""
    contexts = []

    # Extract sanitizers from BIT if available
    selected_sanitizers = []
    bit = None
    if attr_cg and attr_cg.bit_node and attr_cg.bit_node.bit_info:
        bit = attr_cg.bit_node.bit_info
        selected_sanitizers = extract_sanitizers_from_bit(bit, sanitizer)
        if selected_sanitizers:
            logger.info(
                f"Using sanitizers from BIT: {selected_sanitizers} for {cg.name}"
            )

    for _ in range(num_tests):
        context = BlobGenContext(
            harness_name=harness_name,
            sanitizer=sanitizer,
            cg_name=cg.name,
            attr_cg=attr_cg,
            bit=bit,
            selected_sanitizers=selected_sanitizers,
        )
        contexts.append(context)
    return contexts


def _process_bit_for_cgs(
    bit: BugInducingThing,
    harness,
    harness_name: str,
    sanitizer: str,
    previous_crashes,
    visited_cg: Set,
    num_tests: int,
    found_bits,
    CGs: Dict[str, List],
    gc: GlobalContext,
) -> List[BlobGenContext]:
    """Process a BIT against all CGs and create contexts for matching ones."""
    contexts: List[BlobGenContext] = []

    for cg in CGs[harness_name]:
        # Find matching BIT for this CG
        matching_bit = find_matching_bit(cg, [bit])

        if not matching_bit:
            continue

        # Check if this path has already led to a crash
        if is_path_already_crashed(cg, matching_bit, previous_crashes, found_bits):
            logger.info(
                f"Skipping BIT {bit.func_location.func_name} for"
                f" {cg.name} as it already led to a crash"
            )
            visited_cg.add(cg.name)
            continue

        logger.info(f"Loading BIT {bit.func_location.func_name} for {cg.name} ...")

        # Create AttributeCG with the matching BIT
        attr_cg = AttributeCG.from_cg(
            cg,
            gc.code_indexer,
            coverage_info=None,  # Will be updated during validation
            bit=matching_bit,
            language=gc.cp.language,
            focus_on_bit=False,  # For Debug
        )

        # Create contexts for this CG with the matching BIT
        new_contexts = _create_blobgen_context(
            harness_name, sanitizer, cg, attr_cg, num_tests
        )
        contexts.extend(new_contexts)
        visited_cg.add(cg.name)

        # If current path is harness file, we do not need to search for other CG
        if matching_bit.func_location.file_path == harness.src_path:
            break

        # Match only the first CG and break
        break

    return contexts


def _process_unvisited_cgs(
    harness_name: str,
    sanitizer: str,
    previous_crashes,
    visited_cg: Set,
    CGs: Dict[str, List],
    gc: GlobalContext,
) -> List[BlobGenContext]:
    """Process CGs that haven't been visited yet."""
    contexts = []
    for cg in CGs[harness_name]:
        if cg.name in visited_cg:
            continue

        # Check if this path has already led to a crash
        if is_path_already_crashed(cg, None, previous_crashes):
            logger.info(f"Skipping {cg.name} as it already led to a crash")
            visited_cg.add(cg.name)
            continue

        logger.info(f"Loading un-visited CG {cg.name} ...")

        # Create AttributeCG without bit
        attr_cg = AttributeCG.from_cg(
            cg,
            gc.code_indexer,
            coverage_info=None,  # Will be updated during validation
            bit=None,  # Fallback for BIT is not provided
            language=gc.cp.language,
        )
        visited_cg.add(cg.name)

        # Create a single context for this unvisited CG
        context = _create_blobgen_context(
            harness_name, sanitizer, cg, attr_cg, num_tests=1
        )[0]
        contexts.append(context)

    return contexts


async def run_all_agents(
    state: OrchestratorAgentOverallState,
) -> OrchestratorAgentOverallState:
    """Run all agents concurrently with appropriate dependencies."""
    state = state.copy()

    # Prepare contexts for agents
    blobgen_contexts = state["blobgen_contexts"]

    # Set up semaphore for concurrency control
    max_concurrent = int(os.getenv("ORCHESTRATOR_MAX_CONCURRENT_CG", "5"))

    # Dictionary to store tasks by name
    task_dict = {}

    try:
        # Define async functions for each agent process
        async def run_blobgen():
            # Run BlobGenAgent on all contexts
            return await process_blobgen_from_contexts(
                state["gc"],
                state["llm"],
                blobgen_contexts,
                max_concurrent=max_concurrent,
            )

        async def run_generator():
            # Run GeneratorAgent on all contexts
            return await process_generator_from_contexts(
                state["gc"],
                state["llm"],
                blobgen_contexts,
                state["sanitizer"],
                max_concurrent=max_concurrent,
            )

        async def run_mutator():
            # Run MutatorAgent independently from contexts
            return await process_mutator_from_contexts(
                state["gc"], state["llm"], blobgen_contexts, state["sanitizer"]
            )

        # Create tasks for each enabled agent
        if os.getenv("ORCHESTRATOR_BGA_USE", False):
            logger.info("Creating BlobGenAgent task")
            blobgen_task = asyncio.create_task(run_blobgen())
            task_dict["blobgen"] = blobgen_task
        else:
            logger.info("Skipping BlobGenAgent (disabled)")

        if os.getenv("ORCHESTRATOR_GENERATOR_USE", False):
            logger.info("Creating GeneratorAgent task")
            generator_task = asyncio.create_task(run_generator())
            task_dict["generator"] = generator_task
        else:
            logger.info("Skipping GeneratorAgent (disabled)")

        if os.getenv("ORCHESTRATOR_MUTATOR_USE", False):
            logger.info("Creating MutatorAgent task")
            mutator_task = asyncio.create_task(run_mutator())
            task_dict["mutator"] = mutator_task
        else:
            logger.info("Skipping MutatorAgent (disabled)")

        # Run all tasks concurrently
        if task_dict:
            logger.info(
                f"Running {len(task_dict)} agent tasks concurrently:"
                f" {list(task_dict.keys())}"
            )
            results = await asyncio.gather(*task_dict.values(), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error in result: {result}")
                    import traceback

                    tb_lines = traceback.format_exception(
                        type(result), result, result.__traceback__
                    )
                    logger.error("".join(tb_lines))
        else:
            logger.info("No agent tasks to run")

        # Process results in the same order as the original sequential implementation
        # This guarantees consistent result ordering regardless of task completion order
        blobgen_results = {}
        generator_results = {}
        mutator_results = {}

        # Process BlobGenAgent results (if enabled)
        if "blobgen" in task_dict:
            try:
                blobgen_results = task_dict["blobgen"].result()
            except Exception as e:
                logger.error(f"BlobGenAgent task failed: {e}")

        # Process GeneratorAgent results (if enabled)
        if "generator" in task_dict:
            try:
                generator_results = task_dict["generator"].result()
            except Exception as e:
                logger.error(f"GeneratorAgent task failed: {e}")

        # Process MutatorAgent results (if enabled)
        if "mutator" in task_dict:
            try:
                mutator_results = task_dict["mutator"].result()
            except Exception as e:
                logger.error(f"MutatorAgent task failed: {e}")

        # Update state with results
        state["blobgen_results"] = blobgen_results
        state["generator_results"] = generator_results
        state["mutator_results"] = mutator_results

        # Log completion
        logger.info(
            f"Completed concurrent orchestration with {len(blobgen_results)} BlobGen"
            f" results, {len(generator_results)} Generator results, and"
            f" {len(mutator_results)} Mutator results"
        )

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Error in run_all_agents: {error_msg}")
        state["status"] = "failed"
        state["error"] = {"message": str(e), "traceback": traceback.format_exc()}

    return state
