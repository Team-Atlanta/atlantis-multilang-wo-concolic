"""Analyze coverage node for the GeneratorAgent workflow."""

from loguru import logger

from ..prompts import build_prompts
from ..state import GeneratorAgentOverallState


async def analyze_coverage(
    state: GeneratorAgentOverallState,
) -> GeneratorAgentOverallState:
    """Analyze coverage differences."""
    # Get configuration from environment
    state = state.copy()
    llm = state["llm"]
    payload = state["payload"]

    # Extract function names for logging
    if state.get("src_func") and state.get("dst_func"):
        src_func_name = state["src_func"].func_location.func_name
        dst_func_name = state["dst_func"].func_location.func_name
        sanitizer = ", ".join(state["selected_sanitizers"])
        logger.info(
            f"Analyzing coverage for transition: {src_func_name} -> {dst_func_name}, "
            f"with merged coverage [sanitizer: {sanitizer}]"
        )
    else:
        logger.info("Analyzing coverage information ...")

    # Build messages using the build_prompts function
    messages = state["messages"]
    messages.extend(build_prompts(node_type="analyze"))

    responses = await llm.ainvoke(messages, cache=True, cache_index=-2)
    response = responses[-1]

    state["messages"].pop()  # remove analysis task
    # state["messages"].pop() # remove coverage info
    state["messages"].append(response)
    state["payload"] = payload
    state["error"] = {"phase": "analysis", "status": "success", "details": ""}

    logger.debug("Successfully generated feedback for generator improvement")

    return state
