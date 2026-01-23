"""Plan mutation node for the MutatorAgent workflow."""

import os

from langchain_core.messages import AIMessage
from loguru import logger

from mlla.utils.code_tags import MUTATOR_PLAN_TAG
from mlla.utils.execute_llm_code import collect_tag

from ..prompts import build_prompts
from ..state import MutatorAgentOverallState


def verify_mutation_plan(response: AIMessage) -> str:
    """Verify that the response contains a valid mutation plan."""
    content = response.content

    # Extract the plan from the tags
    plan_texts = collect_tag(content, MUTATOR_PLAN_TAG)
    if not plan_texts:
        raise ValueError(f"No {MUTATOR_PLAN_TAG} found in the response")

    plan_text = plan_texts[-1].strip()
    if not plan_text:
        raise ValueError("Mutation plan is empty")

    return plan_text


def plan_mutation_node(
    state: MutatorAgentOverallState,
) -> MutatorAgentOverallState:
    """Plan a mutation strategy for the current transition."""
    state = state.copy()

    # Get source and destination functions
    src_func = state["src_func"]
    dst_func = state["dst_func"]

    # Get current mutator
    current_mutator = state["current_mutator"]

    try:
        # Extract function names
        src_func_name = src_func.func_location.func_name
        dst_func_name = dst_func.func_location.func_name
        logger.info(
            f"Planning mutation strategy for transition: {src_func_name} ->"
            f" {dst_func_name}"
        )

        # Extend messages with plan-specific prompt
        messages = state["messages"]
        messages.extend(build_prompts(node_type="plan"))

        # Get max retries from environment variable
        max_retries = int(os.getenv("BGA_MUTATOR_MAX_RETRIES", "3"))

        # Use ask_and_repeat_until to ensure we get a valid mutation plan
        mutator_plan = state["llm"].ask_and_repeat_until(
            verify_mutation_plan,
            messages,
            default="",  # Default empty string if all attempts fail
            max_retries=max_retries,
            cache=True,
            cache_index=-2,
        )

        # Check if the LLM response is empty and mark as failed
        if not mutator_plan:
            error_msg = "LLM returned empty mutation plan"
            logger.error(error_msg)
            state["error"] = {
                "phase": "plan",
                "status": "failed",
                "details": error_msg,
            }
            return state

        state["messages"].pop()  # remove plan task message
        state["messages"].extend(build_prompts(mutator_plan=mutator_plan))

        current_mutator["mutator_plan"] = mutator_plan

        # Store the plan in the state
        state["current_mutator"] = current_mutator
        state["error"] = {"phase": "plan", "status": "success", "details": ""}

        logger.debug(
            f"Successfully created mutation plan for {src_func_name} -> {dst_func_name}"
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to create mutation plan: {error_msg}")
        state["error"] = {
            "phase": "plan",
            "status": "failed",
            "details": error_msg,
        }

    return state
