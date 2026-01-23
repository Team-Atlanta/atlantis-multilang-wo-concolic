"""Module for calling MutatorAgent from OrchestratorAgent."""

import asyncio
import os
import traceback
from typing import Dict, List, Tuple

from loguru import logger

from ....utils.attribute_cg import AttributeCG, get_transition_key
from ....utils.context import GlobalContext
from ....utils.coverage import is_transition_covered, load_all_coverage_info
from ...mutator_agent.agent import MutatorAgent
from ...mutator_agent.state import MutatorAgentInputState, MutatorPayload


async def run_mutator_on_transitions(
    gc: GlobalContext, llm, transitions: List[Tuple[AttributeCG, Tuple]]
) -> Dict[str, MutatorPayload]:
    """Run MutatorAgent on transitions."""
    mutator_results = {}

    # Create MutatorAgent instance
    mutator_agent = MutatorAgent(gc).compile()

    # Process each transition with MutatorAgent
    async def process_transition(attr_cg, transition):
        src_func, dst_func = transition

        try:
            # Prepare mutator agent input state
            mutator_input = MutatorAgentInputState(
                harness_name=gc.target_harness,
                attr_cg=attr_cg,
                src_func=src_func,
                dst_func=dst_func,
            )

            # Run mutator agent
            result = await mutator_agent.ainvoke(mutator_input)
            return result.get("mutator_dict", {})
        except Exception as e:
            error_msg = f"{e}\n{traceback.format_exc()}"
            logger.error(f"Failed to run mutator agent: {error_msg}")
            return {}

    # Create tasks for all transitions
    tasks = [
        process_transition(attr_cg, transition) for attr_cg, transition in transitions
    ]

    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Mutator task failed: {result}")

            tb_lines = traceback.format_exception(
                type(result), result, result.__traceback__
            )
            logger.warning("".join(tb_lines))
            continue
        elif result:
            mutator_results.update(result)

    return mutator_results


async def process_mutator_from_contexts(
    gc: GlobalContext, llm, blobgen_contexts: List, sanitizer: str
) -> Dict[str, MutatorPayload]:
    """Process MutatorAgent independently from BlobGenAgent contexts."""
    # Get harness information
    harness_name = gc.cur_harness.name
    language = gc.cp.language

    # Step 1: Collect all AttributeCGs from contexts and their transitions
    all_transitions = []
    for context in blobgen_contexts:
        attr_cg = context.get("attr_cg")
        if not attr_cg:
            continue

        # Find unique transitions for this context
        transitions = attr_cg.find_unique_transitions()
        if not transitions:
            continue

        # Add each transition to the list
        for transition in transitions:
            src_func, dst_func = transition
            transition_key = get_transition_key(src_func, dst_func)
            all_transitions.append((attr_cg, transition, transition_key))

    # Log collection results
    logger.info(f"[1] Collected {len(all_transitions)} transitions from AttributeCGs")
    if not all_transitions:
        logger.info("No transitions found in any AttributeCG")
        return {}

    # Step 2: Dedup (keeping the first occurrence) and filter based on file path
    total_transitions_before = len(all_transitions)
    filter_dedup_among_cgs = os.getenv("ORCHESTRATOR_MUTATOR_DEDUP_AMONG_CGS", True)
    transition_to_attr_cg = {}

    for attr_cg, transition, transition_key in all_transitions:
        src_func, dst_func = transition
        src_func_name = src_func.func_location.func_name
        src_file_path = src_func.func_location.file_path
        dst_func_name = dst_func.func_location.func_name
        dst_file_path = dst_func.func_location.file_path

        # Skip transitions without file paths
        if not src_file_path or not dst_file_path:
            logger.warning(
                f"[2] Filtered out function without file path: {src_func_name} -> "
                f"{dst_func_name}"
            )
            continue

        # Add transition (with or without dedup check)
        if not filter_dedup_among_cgs or transition_key not in transition_to_attr_cg:
            transition_to_attr_cg[transition_key] = (attr_cg, transition)

    # Log results
    total_transitions_after = len(transition_to_attr_cg)
    delta = total_transitions_before - total_transitions_after
    dedup_msg = "deduplication" if filter_dedup_among_cgs else "deduplication skipped"
    logger.info(
        f"[2] Transitions after {dedup_msg}: {total_transitions_before} ->"
        f" {total_transitions_after} ({delta})"
    )

    # Step 3: Filter out transitions that are already covered by fuzzers
    filter_already_in_coverage = os.getenv(
        "ORCHESTRATOR_MUTATOR_FILTER_ALREADY_IN_COVERAGE", False
    )
    coverage_filtered_transitions = {}

    if filter_already_in_coverage:
        coverage_per_seed = load_all_coverage_info(gc.fuzzdb, harness_name)
        logger.info(f"Loaded coverage information for {len(coverage_per_seed)} seeds")

        for transition_key, (attr_cg, transition) in transition_to_attr_cg.items():
            src_func, dst_func = transition

            # Check if this transition is already covered
            if coverage_per_seed and is_transition_covered(
                src_func.func_location.func_name,
                src_func.func_location.file_path,
                dst_func.func_location.func_name,
                dst_func.func_location.file_path,
                coverage_per_seed,
                language,
            ):
                logger.debug(
                    "[3] Filtered out already covered transition:"
                    f" {src_func.func_location.func_name} ->"
                    f" {dst_func.func_location.func_name}"
                )
                continue

            coverage_filtered_transitions[transition_key] = (attr_cg, transition)
    else:
        coverage_filtered_transitions = transition_to_attr_cg.copy()

    # Log results
    delta = len(transition_to_attr_cg) - len(coverage_filtered_transitions)
    coverage_msg = (
        "coverage filtering"
        if filter_already_in_coverage
        else "coverage filtering skipped"
    )
    logger.info(
        f"[3] Transitions after {coverage_msg}: {len(transition_to_attr_cg)} ->"
        f" {len(coverage_filtered_transitions)} ({delta})"
    )

    # Step 4: Opt - Filter out transitions if there is no bit_node in attr_cg
    filter_no_bit = os.getenv("ORCHESTRATOR_MUTATOR_FILTER_NO_BIT_CGS", False)
    if filter_no_bit:
        bit_node_filtered_transitions = {}
        for transition_key, (
            attr_cg,
            transition,
        ) in coverage_filtered_transitions.items():
            if attr_cg and not attr_cg.bit_node:
                logger.debug(
                    f"[4] Skipping as its AttributeCG has no bit_node: {transition_key}"
                )
                continue
            bit_node_filtered_transitions[transition_key] = (attr_cg, transition)

        # Update filtered transitions
        before_count = len(coverage_filtered_transitions)
        after_count = len(bit_node_filtered_transitions)
        delta = before_count - after_count
        coverage_filtered_transitions = bit_node_filtered_transitions
        logger.info(
            f"[4] Transitions after bit_node filtering: {before_count} -> "
            f"{after_count} ({delta})"
        )

    # Step 5: Opt - Filter out transitions w/o key_conditions, should_be_taken_lines
    filter_no_conditions = os.getenv("ORCHESTRATOR_MUTATOR_FILTER_NO_CONDITIONS", False)
    if filter_no_conditions:
        condition_filtered_transitions = {}
        for transition_key, (
            attr_cg,
            transition,
        ) in coverage_filtered_transitions.items():
            src_func, dst_func = transition

            src_has_conditions = (
                src_func.key_conditions or src_func.should_be_taken_lines
            )
            dst_has_conditions = (
                dst_func.key_conditions or dst_func.should_be_taken_lines
            )

            if not src_has_conditions and not dst_has_conditions:
                logger.debug(
                    f"[5] Skipping transition without conditions: {transition_key}"
                )
                continue

            condition_filtered_transitions[transition_key] = (attr_cg, transition)

        # Update filtered transitions
        before_count = len(coverage_filtered_transitions)
        after_count = len(condition_filtered_transitions)
        delta = before_count - after_count
        coverage_filtered_transitions = condition_filtered_transitions
        logger.info(
            f"[5] Transitions after condition filtering: {before_count} -> "
            f"{after_count} ({delta})"
        )

    # Log final filtering results
    initial_count = total_transitions_before
    final_count = len(coverage_filtered_transitions)
    delta = initial_count - final_count
    logger.info(
        f"[-] Final transitions after all filtering: {initial_count} -> "
        f"{final_count} ({delta})"
    )

    # If all transitions were filtered out, return early
    if not coverage_filtered_transitions:
        logger.info(
            "[-] All transitions are already covered or filtered out, no need to run "
            "MutatorAgent"
        )
        return {}

    # Convert dictionary to list for run_mutator_on_transitions
    filtered_transitions = [
        (attr_cg, transition)
        for attr_cg, transition in coverage_filtered_transitions.values()
    ]

    # Run MutatorAgent on filtered transitions
    logger.info(f"Running MutatorAgent on {len(filtered_transitions)} transitions")
    return await run_mutator_on_transitions(gc, llm, filtered_transitions)
