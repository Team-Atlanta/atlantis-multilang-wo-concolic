"""Update interesting functions node for the GeneratorAgent workflow."""

import os
from functools import partial
from typing import Dict, List, Optional, Tuple

from langchain_core.messages import BaseMessage, HumanMessage
from loguru import logger

from mlla.utils.code_tags import INTERESTING_FUNC_TAG
from mlla.utils.coverage import annotate_funcs_with_coverage
from mlla.utils.execute_llm_code import collect_tag

from ..prompts.update_interesting_prompts import (
    FUNCTION_SELECTION_PROMPT,
    build_interesting_functions_prompt,
)
from ..state import GeneratorAgentOverallState


def verify_function_selection(
    response, available_functions: List[str], max_functions: int = 10
) -> Optional[Tuple[BaseMessage, List[str]]]:
    """Verify that the response contains valid function selections."""
    content = response.content

    try:
        # Extract function list using collect_tag utility
        func_list_str = collect_tag(content, INTERESTING_FUNC_TAG)[-1]

        # Check if LLM returned NONE (no interesting functions)
        if func_list_str.strip().upper() == "NONE":
            return response, []

        selected_functions = [f.strip() for f in func_list_str.split(",") if f.strip()]
    except (IndexError, AttributeError):
        raise ValueError(f"No {INTERESTING_FUNC_TAG} tags found")

    if len(selected_functions) == 0:
        raise ValueError(
            "No functions are found. If there are no interesting functions, use 'NONE'"
        )

    if len(selected_functions) > max_functions:
        raise ValueError(
            f"Too many functions selected: {len(selected_functions)}. Maximum:"
            f" {max_functions}"
        )

    # Validate each function exists in coverage data
    validated_functions = []
    for func in selected_functions:
        if func not in available_functions:
            error_msg = f"Function `{func}` not found in coverage data."
            # This would be too much.
            # error_msg += f"\nAvailable functions: ..."
            raise ValueError(error_msg)
        validated_functions.append(func)

    if len(validated_functions) == 0:
        raise ValueError("No valid functions found in selection")

    return response, validated_functions


async def select_interesting_functions_llm(
    state: GeneratorAgentOverallState,
) -> List[str]:
    """Use LLM to select interesting functions with retry mechanism."""
    try:
        # Get coverage diff information from payload
        payload = state["payload"]
        coverage_diff = payload.get("coverage_diff", {})
        merged_coverage = payload.get("merged_coverage", {})

        # Get available functions from coverage diff (prioritize functions with changes)
        new_functions = coverage_diff.get("new_functions", [])
        functions_with_new_lines = list(coverage_diff.get("new_lines", {}).keys())

        # Combine and deduplicate
        priority_functions = list(set(new_functions + functions_with_new_lines))

        # If no coverage changes, fall back to all available functions
        if not priority_functions:
            available_functions = list(merged_coverage.keys())
        else:
            # Include priority functions plus some from merged coverage for context
            available_functions = (
                priority_functions
                + [
                    func
                    for func in merged_coverage.keys()
                    if func not in priority_functions
                ][:20]
            )  # Limit to avoid overwhelming the LLM

        # Create validation function with parameters
        verification_func = partial(
            verify_function_selection,
            available_functions=available_functions,
            max_functions=10,
        )

        # Build messages using coverage diff information
        messages = state["messages"]
        messages.append(HumanMessage(content=FUNCTION_SELECTION_PROMPT))

        # Get max retries from environment
        max_retries = int(os.getenv("GENERATOR_MAX_RETRIES", "3"))

        # Use retry mechanism like sanitizer selection
        llm = state["llm"]
        result = await llm.aask_and_repeat_until(
            verification_func,
            messages,
            default=None,
            max_retries=max_retries,
            cache=True,
            cache_index=-2,
        )

        if not result:
            logger.warning(
                f"Failed to select interesting functions after {max_retries} attempts"
            )
            return []

        response, selected_functions = result
        if not response:
            logger.warning("LLM returned empty response")
            return []

        if not selected_functions:
            logger.warning("LLM returned no insteresting functions selected")
            return []

        logger.info(
            f"LLM selected {len(selected_functions)} functions: {selected_functions}"
        )
        return selected_functions

    except Exception as e:
        logger.warning(f"LLM function selection failed: {e}, falling back to heuristic")
        # We will prepare heuristics later.
        # return select_interesting_functions_heuristic(merged_coverage)
        return []


async def update_interesting_functions(
    state: GeneratorAgentOverallState,
) -> GeneratorAgentOverallState:
    """Select interesting functions and extract their source code bodies."""
    state = state.copy()

    # Extract function names for logging
    if state.get("src_func") and state.get("dst_func"):
        src_func_name = state["src_func"].func_location.func_name
        dst_func_name = state["dst_func"].func_location.func_name
        sanitizer = ", ".join(state["selected_sanitizers"])
        logger.info(
            f"Updating interesting functions for transition: {src_func_name} ->"
            f" {dst_func_name} [sanitizer: {sanitizer}]"
        )
    else:
        logger.info("Updating interesting functions ...")

    try:
        # LLM-based function selection
        interesting_functions = await select_interesting_functions_llm(state)

        # Create prompt with interesting functions
        if interesting_functions:
            # Mock implementation: Extract function bodies
            function_bodies = extract_function_bodies(state, interesting_functions)

            interesting_functions_prompt = build_interesting_functions_prompt(
                function_bodies
            )
            state["messages"].pop()  # pop interesting function selection
            state["messages"].append(interesting_functions_prompt)
            logger.info(
                f"Added {len(function_bodies)} interesting functions to messages"
            )

        else:
            # pop interesting function selection message.
            state["messages"].pop()
            logger.info("No interesting functions found to add")

    except Exception as e:
        logger.error(f"Failed to update interesting functions: {e}")
        # Graceful fallback - continue without function bodies
        logger.warning(f"Continuing without interesting functions due to error: {e}")

    return state


def select_interesting_functions_heuristic(merged_coverage: Dict) -> List[str]:
    """Mock function selection logic."""
    # Mock implementation: Select functions based on simple criteria
    interesting_functions = []

    # Mock selection criteria:
    # 1. Functions with coverage (basic filter)
    # 2. Functions with "interesting" names (parse, decode, read, etc.)
    # 3. Limit to top 5 functions for now

    interesting_patterns = [
        "parse",
        "decode",
        "process",
        "handle",
        "validat",
        "check",
        "sanitiz",
        "read",
        "write",
    ]

    scored_functions = []
    for func_name, coverage_info in merged_coverage.items():
        if not coverage_info.get("lines"):
            continue

        score = 0
        func_lower = func_name.lower()

        # Score based on name patterns
        for pattern in interesting_patterns:
            if pattern in func_lower:
                score += 1

        # Score based on coverage lines count
        line_count = len(coverage_info.get("lines", []))
        if line_count > 5:
            score += 1
        if line_count > 20:
            score += 1

        if score > 0:
            scored_functions.append((func_name, score))

    # Sort by score and take top 5
    scored_functions.sort(key=lambda x: x[1], reverse=True)
    interesting_functions = [func for func, _ in scored_functions[:5]]

    logger.debug(f"Selected functions: {interesting_functions}")
    return interesting_functions


def extract_function_bodies(
    state: GeneratorAgentOverallState, function_names: List[str]
) -> Dict[str, str]:
    """Extract function bodies from source files using coverage information."""
    try:
        # Get merged coverage from payload
        merged_coverage = state["payload"]["merged_coverage"]

        # Filter coverage to only include selected functions
        filtered_coverage = {
            func_name: coverage_info
            for func_name, coverage_info in merged_coverage.items()
            if func_name in function_names
        }

        if not filtered_coverage:
            logger.warning("No coverage information found for selected functions")
            return {}

        # Print function name and path information
        logger.info("Selected interesting functions:")
        for func_name, coverage_info in filtered_coverage.items():
            func_path = coverage_info.get("src", "Unknown path")
            logger.info(f"  - Function: {func_name}, Path: {func_path}")

        # Use new function-targeted coverage annotation
        annotated_sources = annotate_funcs_with_coverage(filtered_coverage)

        logger.info(f"Extracted source code for {len(filtered_coverage)} functions")
        return {"annotated_sources": annotated_sources}

    except Exception as e:
        logger.error(f"Failed to extract function bodies: {e}")
        return {}
