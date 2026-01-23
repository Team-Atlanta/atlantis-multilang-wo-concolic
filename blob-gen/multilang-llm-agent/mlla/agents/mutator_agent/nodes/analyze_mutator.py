"""Analyze mutator node for the MutatorAgent workflow."""

import os

from langchain_core.messages import AIMessage
from loguru import logger

from mlla.utils.code_tags import FEEDBACK_TAG
from mlla.utils.execute_llm_code import collect_tag

from ..prompts import build_prompts
from ..state import MutatorAgentOverallState


def verify_mutator_analysis(response: AIMessage) -> str:
    """Verify that the response contains valid mutator analysis."""
    content = response.content

    # Extract the feedback from the tags
    feedback_texts = collect_tag(content, FEEDBACK_TAG)
    if not feedback_texts:
        raise ValueError(f"No {FEEDBACK_TAG} found in the response")

    feedback_text = feedback_texts[-1].strip()
    if not feedback_text:
        raise ValueError("Mutator feedback is empty")

    return feedback_text


def analyze_mutator_node(
    state: MutatorAgentOverallState,
) -> MutatorAgentOverallState:
    """Analyze the current mutator and provide feedback."""
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
            f"Analyzing mutator for transition: {src_func_name} -> {dst_func_name}"
        )

        # Extend messages with analyze-specific prompt
        messages = state["messages"]
        messages.extend(build_prompts(node_type="analyze"))

        # Get max retries from environment variable
        max_retries = int(os.getenv("BGA_MUTATOR_MAX_RETRIES", "3"))

        # Use ask_and_repeat_until to ensure we get valid analysis
        analysis = state["llm"].ask_and_repeat_until(
            verify_mutator_analysis,
            messages,
            default="",  # Default empty string if all attempts fail
            max_retries=max_retries,
            cache=True,
            cache_index=-2,
        )

        # Check if the LLM response is empty and mark as failed
        if not analysis:
            error_msg = "LLM returned empty mutator analysis"
            logger.error(error_msg)
            state["error"] = {
                "phase": "analyze",
                "status": "failed",
                "details": error_msg,
            }
            return state

        state["messages"].pop()  # remove analyze task message
        # Add the analysis feedback to messages for accumulation
        state["messages"].extend(build_prompts(mutator_feedback=analysis))

        current_mutator["mutator_feedback"] = analysis

        # Store the analysis in the state
        state["current_mutator"] = current_mutator
        state["error"] = {"phase": "analyze", "status": "success", "details": ""}

        logger.debug(
            f"Successfully analyzed mutator for {src_func_name} -> {dst_func_name}"
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to analyze mutator: {error_msg}")
        state["error"] = {
            "phase": "analyze",
            "status": "failed",
            "details": error_msg,
        }

    return state
