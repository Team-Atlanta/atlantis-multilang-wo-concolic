"""Graph builder for MutatorAgent."""

import os
from typing import TYPE_CHECKING, Literal

from langgraph.graph import StateGraph
from loguru import logger

from mlla.utils.code_tags import MUTATOR_COMPLETED

if TYPE_CHECKING:
    from .agent import MutatorAgent

from .nodes import analyze_mutator_node, create_mutator_node, plan_mutation_node
from .state import MutatorAgentOverallState


def build_main_graph(agent: "MutatorAgent") -> StateGraph:
    """Build the main graph for the MutatorAgent."""
    # Create a new graph builder
    builder = agent.builder

    # Add nodes
    builder.add_node("plan_mutation", plan_mutation_node)
    builder.add_node("create_mutator", create_mutator_node)
    builder.add_node("analyze_mutator", analyze_mutator_node)

    # Define the flow
    builder.add_edge("preprocess", "plan_mutation")

    # Add conditional edge after planning to check for failures
    builder.add_conditional_edges(
        "plan_mutation",
        should_create_mutator,
        ["create_mutator", "finalize"],
    )

    # Add conditional edges for improvement loop
    builder.add_conditional_edges(
        "create_mutator",
        should_analyze_mutator,
        ["analyze_mutator", "finalize"],
    )

    builder.add_conditional_edges(
        "analyze_mutator",
        should_improve_mutator,
        ["create_mutator", "finalize"],
    )

    # Compile the graph
    return builder.compile()


def should_create_mutator(
    state: MutatorAgentOverallState,
) -> Literal["create_mutator", "finalize"]:
    """Determine if we should proceed to create mutator after planning."""
    # Check error status from planning phase
    error = state.get("error", {})
    if error.get("status") == "failed":
        logger.warning(f"Planning failed: {error.get('details')}")
        return "finalize"

    # Check if planning produced a valid plan
    current_mutator = state["current_mutator"]
    if not current_mutator.get("mutator_plan"):
        logger.warning("Planning did not produce a valid mutation plan")
        return "finalize"

    return "create_mutator"


def should_analyze_mutator(
    state: MutatorAgentOverallState,
) -> Literal["analyze_mutator", "finalize"]:
    """Determine if we should analyze the mutator."""
    # Check error status from creation phase
    error = state.get("error", {})
    if error.get("status") == "failed":
        logger.warning(f"Mutator creation failed: {error.get('details')}")
        return "finalize"

    # Check if current_mutator has required fields
    current_mutator = state["current_mutator"]
    if not current_mutator.get("mutator_code") or not current_mutator.get(
        "mutator_desc"
    ):
        logger.warning("Current mutator is missing code or description")
        return "finalize"

    # Check if mutator improvement is disabled
    max_iter = int(os.getenv("BGA_MUTATOR_MAX_ITERATION", "1"))
    if max_iter <= 0:
        logger.info("Mutator improvement disabled")
        return "finalize"

    # Check if we've reached the maximum number of iterations
    iter_cnt = state.get("iter_cnt", 1)
    if iter_cnt >= max_iter:
        logger.info(f"Max iterations ({max_iter}) reached")
        return "finalize"

    return "analyze_mutator"


def should_improve_mutator(
    state: MutatorAgentOverallState,
) -> Literal["create_mutator", "finalize"]:
    """Determine if we should improve the mutator"""
    # Check error status from analysis phase
    error = state.get("error", {})
    if error.get("status") == "failed":
        logger.warning(f"Mutator analysis failed: {error.get('details')}")
        return "finalize"

    # Check if current_mutator has required fields
    current_mutator = state["current_mutator"]
    if not current_mutator.get("mutator_code") or not current_mutator.get(
        "mutator_desc"
    ):
        logger.warning("Current mutator is missing code or description")
        return "finalize"

    if not current_mutator.get("mutator_feedback"):
        logger.warning("Current mutator is missing feedback for improvement")
        return "finalize"

    # Check if the LLM indicated the mutator is complete
    if MUTATOR_COMPLETED in current_mutator["mutator_feedback"]:
        logger.info("Mutator analysis thinks the task is completed")
        return "finalize"

    return "create_mutator"
