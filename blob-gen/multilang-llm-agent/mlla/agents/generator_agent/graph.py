"""Graph builder for GeneratorAgent."""

import os
from typing import TYPE_CHECKING, Literal

from langgraph.graph import StateGraph
from loguru import logger

if TYPE_CHECKING:
    from .agent import GeneratorAgent

from .nodes import (
    analyze_coverage,
    collect_coverage,
    create_generator,
    plan_generator,
    select_sanitizer,
    update_interesting_functions,
)

# from .prompts import GENERATOR_COMPLETED
from .state import GeneratorAgentOverallState


def build_main_graph(agent: "GeneratorAgent") -> StateGraph:
    """Build the main graph for the GeneratorAgent."""
    # Create a new graph builder
    builder = agent.builder

    # Add nodes
    builder.add_node("select_sanitizer", select_sanitizer)
    builder.add_node("plan_generator", plan_generator)
    builder.add_node("create_generator", create_generator)
    builder.add_node("collect_coverage", collect_coverage)
    builder.add_node("update_interesting_functions", update_interesting_functions)
    builder.add_node("analyze_coverage", analyze_coverage)

    # Define the flow
    # Add conditional edge for crash detection
    builder.add_conditional_edges(
        "preprocess",
        lambda state: (
            "select_sanitizer"
            if state.get("standalone") and state.get("run_sanitizer_selection")
            else "plan_generator"
        ),
    )

    builder.add_edge("select_sanitizer", "plan_generator")
    builder.add_edge("plan_generator", "create_generator")
    builder.add_edge("create_generator", "collect_coverage")

    # Add conditional edge for interesting functions update
    builder.add_conditional_edges(
        "collect_coverage",
        should_update_interesting_functions,
        ["update_interesting_functions", "analyze_coverage", "finalize"],
    )

    # Add conditional edge for crash detection
    builder.add_conditional_edges(
        "update_interesting_functions",
        should_analyze_coverage,
        ["analyze_coverage", "finalize"],
    )

    # Add conditional edge for retries
    builder.add_conditional_edges(
        "analyze_coverage",
        should_improve_generator,
        ["create_generator", "finalize"],
    )

    # Compile the graph
    return builder.compile()


def should_update_interesting_functions(
    state: GeneratorAgentOverallState,
) -> Literal["update_interesting_functions", "analyze_coverage", "finalize"]:
    """Determine if we should update interesting functions."""
    max_iter = int(os.getenv("BGA_GENERATOR_MAX_ITERATION", "1"))
    iter_cnt = state.get("iter_cnt", 1)

    payload = state.get("payload")
    if not payload:
        logger.warning("No payload, skipping interesting functions update")
        return "finalize"

    # if standalone mode, we need to keep going
    if state.get("crashed"):
        logger.info("Crash found, stopping generator loop")
        return "finalize"

    if not payload.get("merged_coverage"):
        logger.warning(
            "No merged coverage information, skipping interesting functions update"
        )
        return "finalize"

    if iter_cnt >= max_iter:
        logger.info(f"Max iterations ({max_iter}) reached, finalizing")
        return "finalize"

    # if not state["standalone"]:
    #     return "analyze_coverage"

    return "update_interesting_functions"


def should_analyze_coverage(
    state: GeneratorAgentOverallState,
) -> Literal["analyze_coverage", "finalize"]:
    """Determine if we should continue to analyze coverage."""
    max_iter = int(os.getenv("BGA_GENERATOR_MAX_ITERATION", "1"))
    iter_cnt = state.get("iter_cnt", 1)

    payload = state.get("payload")
    if not payload:
        logger.warning("No payload, stopping generator loop")
        return "finalize"

    # if standalone mode, we need to keep going
    if state.get("crashed"):
        logger.info("Crash found, stopping generator loop")
        return "finalize"

    if not payload.get("merged_coverage"):
        logger.warning(
            "No merged coverage information, skipping interesting functions update"
        )
        return "finalize"

    if iter_cnt >= max_iter:
        logger.info(f"Max iterations ({max_iter}) reached, finalizing")
        return "finalize"

    return "analyze_coverage"


def should_improve_generator(
    state: GeneratorAgentOverallState,
) -> Literal["create_generator", "finalize"]:
    """Determine if we should try another improvement iteration."""
    max_iter = int(os.getenv("BGA_GENERATOR_MAX_ITERATION", "1"))
    iter_cnt = state.get("iter_cnt", 1)

    payload = state.get("payload")
    if not payload:
        logger.warning("No payload, stopping generator loop")
        return "finalize"

    # if standalone mode, we need to keep going
    if state.get("crashed"):
        logger.info("Crash found, stopping generator loop")
        return "finalize"

    # last_msg = state["messages"][-1]
    # if last_msg and GENERATOR_COMPLETED in last_msg.content:
    #     logger.info("Generator analysis thinks the task is completed.")
    #     return "finalize"

    if iter_cnt >= max_iter:
        logger.info(f"Max iterations ({max_iter}) reached, finalizing")
        return "finalize"

    return "create_generator"
