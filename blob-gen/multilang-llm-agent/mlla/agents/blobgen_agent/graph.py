# import datetime
import os
from typing import TYPE_CHECKING, Literal

from langgraph.graph import StateGraph
from loguru import logger

if TYPE_CHECKING:
    from .agent import BlobGenAgent

from .nodes import (
    analyze_failure,
    collect_coverage_node,
    generate_payload,
    select_sanitizer_node,
)
from .state import BlobGenAgentOverallState


def build_main_graph(agent: "BlobGenAgent") -> StateGraph:
    """Build the workflow graph for processing a single CG."""
    builder = agent.builder

    # Add nodes
    builder.add_node("select_sanitizer", select_sanitizer_node)
    builder.add_node("generate_payload", generate_payload)
    builder.add_node("collect_coverage", collect_coverage_node)
    builder.add_node("analyze_failure", analyze_failure)

    # Define conditional edge from preprocess based on selected sanitizers
    builder.add_conditional_edges(
        "preprocess",
        should_select_sanitizer,
        ["select_sanitizer", "generate_payload"],
    )
    builder.add_edge("select_sanitizer", "generate_payload")

    # Conditional edges based on sanitizer selection
    builder.add_conditional_edges(
        "select_sanitizer",
        should_generate_payload,
        ["generate_payload", "finalize"],
    )

    # Conditional edges based on coverage collection
    builder.add_conditional_edges(
        "generate_payload",
        should_collect_coverage,
        ["collect_coverage", "finalize"],
    )
    builder.add_conditional_edges(
        "collect_coverage",
        should_analyze_failure,
        ["analyze_failure", "finalize"],
    )
    builder.add_conditional_edges(
        "analyze_failure",
        should_retry_generation,
        ["generate_payload", "finalize"],
    )

    return builder.compile()


def should_select_sanitizer(
    state: BlobGenAgentOverallState,
) -> Literal["select_sanitizer", "generate_payload"]:
    """Determine if we should select sanitizers or use pre-selected ones."""
    # Check if sanitizer selection is disabled
    if not state.get(
        "run_sanitizer_selection", True
    ):  # Default to True for backward compatibility
        logger.info("Sanitizer selection disabled, proceeding to payload generation")
        return "generate_payload"

    if state.get("selected_sanitizers"):
        logger.info(f"Using pre-selected sanitizers: {state['selected_sanitizers']}")
        return "generate_payload"

    return "select_sanitizer"


def should_generate_payload(
    state: BlobGenAgentOverallState,
) -> Literal["generate_payload", "finalize"]:
    """Determine if we should generate payloads or finalize."""
    # If no sanitizers were selected, skip to finalize
    if not state.get("selected_sanitizers"):
        logger.error("No sanitizers selected, skipping payload generation.")
        return "finalize"

    return "generate_payload"


def should_collect_coverage(
    state: BlobGenAgentOverallState,
) -> Literal["collect_coverage", "finalize"]:
    """Determine if we should collect coverage for the payload or finalize."""

    # Check if the payload has been generated
    current_payload = state.get("current_payload", {})
    if not current_payload.get("code") or not current_payload.get("blob"):
        logger.warning(
            "No payload code and blob available, skipping coverage collection"
        )
        return "finalize"

    logger.debug("Payload blob available, proceeding to collect coverage")
    return "collect_coverage"


def should_analyze_failure(
    state: BlobGenAgentOverallState,
) -> Literal["analyze_failure", "finalize"]:
    """Determine if we should analyze failure or finalize."""

    # (This would be implemented in the agent.py file)
    max_iter = int(os.getenv("BGA_MAX_ITERATION", "3"))

    # Check coverage collection status
    if state["status"] == "crashed":
        # If we found crashes, no need to analyze failures
        logger.info("Payload crashed, skipping failure analysis")
        return "finalize"

    # Check if the payload has coverage collected and has run_pov_result
    current_payload = state.get("current_payload", {})
    if not current_payload.get("run_pov_result"):
        logger.warning("Payload has no coverage data, skipping failure analysis")
        return "finalize"

    # If payload is already crashed, no need to analyze
    if current_payload.get("crashed", False):
        logger.info("Payload already crashed, skipping failure analysis")
        return "finalize"

    if state["iter_cnt"] >= max_iter:
        logger.warning(f"Max retries reached ({max_iter}), finalizing")
        return "finalize"

    # If we haven't found crashes but haven't reached max retries, analyze failures
    logger.debug("Analyzing payload failure")
    return "analyze_failure"


def should_retry_generation(
    state: BlobGenAgentOverallState,
) -> Literal["generate_payload", "finalize"]:
    """Determine if we should retry payload generation or finalize."""

    # (This would be implemented in the agent.py file)
    max_iter = int(os.getenv("BGA_MAX_ITERATION", "3"))

    # Check if we've reached max retries
    if state["iter_cnt"] >= max_iter:
        logger.warning(f"Max blob generation retries reached ({max_iter})")
        return "finalize"

    # Check if we have failure explanations to use for retry
    current_payload = state.get("current_payload", {})
    has_failure_explanation = current_payload.get("failure_explanation")

    if not has_failure_explanation:
        # If no failure explanations, no point in retrying
        logger.warning("No failure explanation available, finalizing")
        return "finalize"

    logger.debug("Retrying payload generation with failure analysis")
    return "generate_payload"
