"""Plan generator node for the GeneratorAgent workflow."""

from loguru import logger

from ..prompts import build_prompts
from ..state import GeneratorAgentOverallState


async def plan_generator(
    state: GeneratorAgentOverallState,
) -> GeneratorAgentOverallState:
    """Create a plan for the generator."""
    # Get configuration from environment
    state = state.copy()
    llm = state["llm"]

    # Extract function names for logging
    if state.get("src_func") and state.get("dst_func"):
        src_func_name = state["src_func"].func_location.func_name
        dst_func_name = state["dst_func"].func_location.func_name
        sanitizer = ", ".join(state["selected_sanitizers"])
        logger.info(
            f"Planning generation strategy for transition: {src_func_name} -> "
            f"{dst_func_name} [sanitizer: {sanitizer}]"
        )
    else:
        logger.info("Planning generation strategy ...")

    messages = state["messages"]
    messages.extend(build_prompts(node_type="plan"))

    responses = await llm.ainvoke(messages, cache=True, cache_index=-2)
    response = responses[-1]

    state["messages"].pop()  # remove plan task
    state["messages"].append(response)

    # Update state
    state["error"] = {"phase": "plan", "status": "success", "details": ""}

    logger.debug("Successfully created generator plan")

    return state
