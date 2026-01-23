import hashlib
import os
import traceback

from loguru import logger

from mlla.utils.artifact_storage import store_artifact
from mlla.utils.attribute_cg import get_transition_key

from ..prompts import build_prompts
from ..state import MutatorAgentOverallState, MutatorPayload
from .common import verify_mutator


def create_mutator_node(
    state: MutatorAgentOverallState,
) -> MutatorAgentOverallState:
    """Create or improve a mutator for the current transition."""
    state = state.copy()
    state["iter_cnt"] += 1

    # Check if this is an improvement iteration
    iter_cnt = state["iter_cnt"]
    is_improvement = iter_cnt > 1

    # Get source and destination functions
    src_func = state["src_func"]
    dst_func = state["dst_func"]

    # Get current mutator
    current_mutator = state["current_mutator"]

    try:
        # Extract function names
        src_func_name = src_func.func_location.func_name
        dst_func_name = dst_func.func_location.func_name

        if is_improvement:
            max_iter = int(os.getenv("BGA_MUTATOR_MAX_ITERATION", "1"))
            logger.info(
                f"Improving mutator for ({iter_cnt}/{max_iter}): "
                f"{src_func_name} -> {dst_func_name}"
            )
            node_type = "improve"
        else:
            logger.info(
                f"Generating mutator for transition: {src_func_name} -> {dst_func_name}"
            )
            node_type = "create"

        # Extend messages with appropriate prompt
        messages = state["messages"]
        messages.extend(build_prompts(node_type=node_type))

        # Get max retries from environment variable
        max_retries = int(os.getenv("BGA_MUTATOR_MAX_RETRIES", "3"))

        # Use ask_and_repeat_until to ensure we get a valid mutator
        mutator_code, mutator_desc = state["llm"].ask_and_repeat_until(
            verify_mutator,
            messages,
            default=("", ""),  # Default empty strings if all attempts fail
            max_retries=max_retries,
            cache=True,
            cache_index=-2,
        )

        # Check if the LLM response is empty and mark as failed
        if not mutator_code or not mutator_desc:
            error_msg = "LLM returned empty mutator code or description"
            logger.error(error_msg)
            state["error"] = {
                "phase": node_type,
                "status": "failed",
                "details": error_msg,
            }
            return state

        logger.debug("Successfully generated and tested the mutator")

        # Create mutator hash
        mutator_hash = hashlib.md5(mutator_code.encode()).hexdigest()

        # Create MutatorPayload
        mutator_payload = MutatorPayload(
            mutator_code=mutator_code,
            mutator_desc=mutator_desc,
            mutator_hash=mutator_hash,
        )

        state["messages"].pop()  # remove create task message
        # Add the mutator results to messages for accumulation
        state["messages"].extend(
            build_prompts(
                mutator_code=mutator_payload["mutator_code"],
                mutator_desc=mutator_payload["mutator_desc"],
            )
        )

        current_mutator.update(mutator_payload)

        # Store the mutator using the new unified artifact storage system
        store_artifact(
            gc=state["gc"],
            agent_name="mutator",
            artifact_type="mutator",
            artifact_code=mutator_payload["mutator_code"],
            artifact_desc=mutator_payload["mutator_desc"],
            artifact_hash=mutator_payload["mutator_hash"],
            iter_cnt=state.get("iter_cnt", 0),
            src_func=src_func,
            dst_func=dst_func,
            store_in_output=True,
        )

        transition_key = get_transition_key(src_func, dst_func)
        state["mutator_dict"][transition_key] = mutator_payload
        state["current_mutator"] = current_mutator
        state["error"] = {"phase": node_type, "status": "success", "details": ""}

        logger.debug(
            f"Successfully {node_type}d mutator for {src_func_name} -> {dst_func_name}"
        )

    except Exception:
        error_msg = traceback.format_exc()
        logger.error(f"Failed to {node_type} mutator: {error_msg}")
        state["error"] = {
            "phase": node_type,
            "status": "failed",
            "details": error_msg,
        }

    return state
