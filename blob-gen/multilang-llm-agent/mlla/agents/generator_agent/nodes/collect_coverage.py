"""Collect coverage node for the GeneratorAgent workflow."""

import hashlib
import os
import traceback
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage
from loguru import logger

from ....modules.sanitizer import is_known_crash
from ....utils.artifact_storage import store_artifact
from ....utils.coverage import (
    compare_coverage,
    filter_coverage_by_func_list,
    print_coverage_diff,
)
from ....utils.run_pov import RunPovResult, process_pov_in_docker_async
from ..prompts.analyze_prompts import COVERAGE_SUMMARY_TEMPLATE
from ..prompts.build_prompts import build_error_prompt
from ..state import GeneratorAgentOverallState
from ..utils import merge_coverage
from .common import execute_generator


def calculate_coverage_stats(
    merged_coverage: Dict[str, Any],
    prev_coverage: Dict[str, Any],
    func_list: List[str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Calculate coverage statistics and differences."""
    # Use pre-computed merged coverage

    filtered_prev_coverage = filter_coverage_by_func_list(
        prev_coverage,
        func_list,
    )
    filtered_merged_coverage = filter_coverage_by_func_list(
        merged_coverage,
        func_list,
    )

    logger.debug(
        f"Filtered coverage from {len(prev_coverage)} to"
        f" {len(filtered_prev_coverage)} functions in previous coverage"
    )
    logger.debug(
        f"Filtered coverage from {len(merged_coverage)} to"
        f" {len(filtered_merged_coverage)} functions in merged coverage"
    )

    # Compare entire coverage with previous
    entire_coverage_diff = compare_coverage(prev_coverage, merged_coverage)

    # Count new and removed functions/files/lines in entire coverage
    new_all_funcs = len(entire_coverage_diff.get("new_functions", []))
    removed_all_funcs = len(entire_coverage_diff.get("removed_functions", []))

    # Count affected files
    new_files = set()
    for func in entire_coverage_diff.get("new_functions", []):
        if func in merged_coverage and merged_coverage[func].get("src"):
            new_files.add(merged_coverage[func]["src"])

    removed_files = set()
    for func in entire_coverage_diff.get("removed_functions", []):
        if func in prev_coverage and prev_coverage[func].get("src"):
            removed_files.add(prev_coverage[func]["src"])

    # Count line changes
    new_all_lines = sum(
        len(lines) for lines in entire_coverage_diff.get("new_lines", {}).values()
    )
    removed_all_lines = sum(
        len(lines) for lines in entire_coverage_diff.get("removed_lines", {}).values()
    )

    # fix inconsistency: if we have removed lines, we have at least one removed func
    if removed_all_lines > 0 and removed_all_funcs == 0:
        # Count functions with removed lines
        removed_all_funcs = len(entire_coverage_diff.get("removed_lines", {}))

        # Recount removed files
        removed_files = set()
        for func in entire_coverage_diff.get("removed_lines", {}):
            if func in prev_coverage and prev_coverage[func].get("src"):
                removed_files.add(prev_coverage[func]["src"])

    # Calculate statistics for primary coverage (functions in attr_cg)
    primary_files = set(
        info.get("src", "")
        for info in filtered_merged_coverage.values()
        if info.get("src")
    )
    primary_lines = sum(
        len(info.get("lines", [])) for info in filtered_merged_coverage.values()
    )

    # Calculate statistics for entire coverage
    all_files = set(
        info.get("src", "") for info in merged_coverage.values() if info.get("src")
    )
    all_lines = sum(len(info.get("lines", [])) for info in merged_coverage.values())

    # Prepare coverage statistics
    coverage_stats = {
        "filtered_func_count": len(filtered_merged_coverage),
        "primary_files_count": len(primary_files),
        "primary_lines_count": primary_lines,
        "total_func_count": len(merged_coverage),
        "total_files_count": len(all_files),
        "total_lines_count": all_lines,
        "new_funcs": new_all_funcs,
        "new_files_count": len(new_files),
        "new_lines": new_all_lines,
        "removed_funcs": removed_all_funcs,
        "removed_files_count": len(removed_files),
        "removed_lines": removed_all_lines,
    }

    # Calculate coverage diff for filtered coverage
    coverage_diff = compare_coverage(filtered_prev_coverage, filtered_merged_coverage)

    return filtered_merged_coverage, coverage_diff, coverage_stats


def analyze_coverage_diff(
    state: GeneratorAgentOverallState,
    merged_coverage: Dict[str, Any],
) -> Dict[str, Any]:
    """Analyze coverage differences and return diff info and formatted string."""
    payload = state["payload"]
    prev_coverage = payload.get("prev_coverage_info", {})

    # Get function list from attr_cg
    attr_cg = state.get("attr_cg")
    if attr_cg:
        func_list = attr_cg.get_func_list()
    else:
        func_list = []

    # Calculate coverage statistics and differences
    filtered_merged_coverage, coverage_diff, coverage_stats = calculate_coverage_stats(
        merged_coverage, prev_coverage, func_list
    )

    # Format coverage diff string
    if coverage_diff:
        coverage_diff_str = print_coverage_diff(coverage_diff)
    else:
        coverage_diff_str = ""

    # Generate summary using the template
    summary_str = COVERAGE_SUMMARY_TEMPLATE.format(**coverage_stats)
    full_coverage_diff_str = f"{summary_str}\n\n{coverage_diff_str}"

    logger.debug(
        "Coverage analysis completed:"
        f" {len(coverage_diff.get('new_functions', []))} new functions,"
        f" {len(coverage_diff.get('new_lines', {}))} functions with new lines"
    )

    # Return analysis results
    analysis_results = {
        "filtered_merged_coverage": filtered_merged_coverage,
        "coverage_diff": coverage_diff,
        "coverage_stats": coverage_stats,
        "coverage_diff_str": full_coverage_diff_str,
    }

    return analysis_results


async def collect_coverage(
    state: GeneratorAgentOverallState,
) -> GeneratorAgentOverallState:
    """Run generator and collect coverage information for generated blobs."""
    # Get configuration from environment
    seed_num = int(os.getenv("BGA_GENERATOR_SEED_NUM", "31337"))
    num_blobs = int(os.getenv("BGA_GENERATOR_NUM_BLOBS", "5"))
    state = state.copy()
    payload = state["payload"]
    harness_name = state["harness_name"]
    crashed_blobs = state["crashed_blobs"]

    # Extract function names for logging
    if state.get("src_func") and state.get("dst_func"):
        src_func_name = state["src_func"].func_location.func_name
        dst_func_name = state["dst_func"].func_location.func_name
        sanitizer = ", ".join(state["selected_sanitizers"])

        logger.info(
            f"Collecting coverage for transition: {src_func_name} -> {dst_func_name},"
            f" generating {num_blobs} blobs with seed {seed_num} [sanitizer:"
            f" {sanitizer}]"
        )
    else:
        logger.info("Collecting coverage information ...")

    # Execute the generator if code is available
    generator_code = payload.get("generator_code")
    generator_hash = payload.get("generator_hash")
    if not generator_code or not generator_hash:
        logger.error("No generator code available")
        state["error"] = {
            "phase": "coverage",
            "status": "failed",
            "details": "No generator code available",
        }
        return state

    blobs: List[bytes] = []
    blob_errors: List[str] = []
    try:
        # Generate and store blobs
        blobs, blob_errors = execute_generator(generator_code, seed_num, num_blobs)
        logger.debug(
            f"Successfully generated {len(blobs)} blobs with {len(blob_errors)} errors"
        )

    except Exception:
        error_msg = traceback.format_exc()
        logger.error(f"Failed to execute generator: {error_msg}")
        state["error"] = {
            "phase": "coverage",
            "status": "failed",
            "details": error_msg,
        }
        return state

    try:
        # Run each blob and collect coverage
        coverage_results = []
        for idx, blob in enumerate(blobs, 1):
            blob_hash = hashlib.md5(blob).hexdigest()

            # Run blob and get coverage
            result: RunPovResult = await process_pov_in_docker_async(
                state["gc"], harness_name, blob_hash, blob, idx, len(blobs)
            )

            crashed = result["triggered"]
            sanitizer_info = result["triggered_sanitizer"]
            coverage_info = result["coverage"]

            # Parse coverage info and annotate source files
            coverage_results.append(
                {
                    "blob_hash": blob_hash,
                    "sanitizer_info": sanitizer_info,
                    "coverage_info": coverage_info,
                }
            )

            # Store crashed blobs
            if crashed:
                logger.debug(f"Storing crashed blob from {generator_hash}")

                # Store using the new unified artifact storage system
                store_artifact(
                    gc=state["gc"],
                    agent_name="generator",
                    artifact_type="crashed_blob",
                    artifact_hash=generator_hash,
                    artifact_code="",
                    artifact_desc=f"Crashed with sanitizer info:\n{sanitizer_info}",
                    artifact_blob=blob,
                    iter_cnt=state["iter_cnt"],
                    src_func=state.get("src_func"),
                    dst_func=state.get("dst_func"),
                    bit_info=state.get("bit"),
                    coverage_info=coverage_info,
                    run_pov_result=result,
                    store_in_output=True,
                )
                crashed_blobs[blob_hash] = blob

                # check sanitizer is in actual target scope
                # if not, we need to do feedback loop again
                # if is_known_crash(sanitizer_info):
                if not state["standalone"] and is_known_crash(sanitizer_info):
                    state["crashed"] = True
                    logger.info(f"Stop running for {sanitizer_info}")
                    break

                else:
                    state["crashed"] = False
                    logger.info(f"Keep running for {sanitizer_info}")

        logger.debug(f"Successfully collected coverage for {len(blobs)} blobs")

        # Merge all coverage results into a single dictionary
        merged_coverage = merge_coverage(coverage_results)

        # Analyze coverage differences
        analysis_results = analyze_coverage_diff(state, merged_coverage)

        # Store the merged coverage information with the same name prefix
        logger.debug(
            f"Storing merged coverage information with generator hash {generator_hash}"
        )

        # Store results and check for crashes
        payload["coverage_results"] = coverage_results
        payload["merged_coverage"] = merged_coverage
        payload["generator_blobs"] = blobs

        # Store coverage analysis results for use in update_interesting_functions
        payload["prev_coverage_info"] = analysis_results["filtered_merged_coverage"]
        payload["coverage_diff"] = analysis_results["coverage_diff"]
        payload["coverage_stats"] = analysis_results["coverage_stats"]
        payload["coverage_diff_str"] = analysis_results["coverage_diff_str"]

        if blob_errors:
            state["messages"].append(build_error_prompt(blob_errors))

        coverage_diff_str = payload.get("coverage_diff_str", "")
        state["messages"].append(HumanMessage(content=coverage_diff_str))

        state["payload"] = payload
        state["crashed_blobs"] = crashed_blobs
        state["error"] = {"phase": "coverage", "status": "success", "details": ""}

        # Store generator using the new unified artifact storage system
        store_artifact(
            gc=state["gc"],
            agent_name="generator",
            artifact_type="coverage",
            artifact_hash=generator_hash,
            artifact_code=generator_code,
            artifact_desc="",
            iter_cnt=state["iter_cnt"],
            src_func=state.get("src_func"),
            dst_func=state.get("dst_func"),
            bit_info=state.get("bit"),
            coverage_info=merged_coverage,
            prompts=state["messages"],
            store_in_output=False,
        )

    except Exception:
        error_msg = traceback.format_exc()
        logger.error(f"Failed to collect coverage: {error_msg}")
        state["error"] = {"phase": "coverage", "status": "failed", "details": error_msg}

    return state
