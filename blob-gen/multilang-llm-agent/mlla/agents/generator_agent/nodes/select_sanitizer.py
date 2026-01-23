from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from typing_extensions import Dict

from ..prompts import build_prompts
from ..prompts.sanitizer_prompts import (
    SANITIZER_SELECTOR_SYSTEM_PROMPT,
    build_sanitizer_prompt,
)
from ..state import GeneratorAgentOverallState


async def select_sanitizer(state: GeneratorAgentOverallState) -> Dict:
    """Select appropriate sanitizer for the current context."""
    new_state: Dict = {}
    sanitizer = state["sanitizer"]
    llm = state["llm"]
    cp_name = state["cp_name"]
    harness_name = state["harness_name"]

    logger.info("Selecting vulnerability type based on the source code ...")

    assert state.get("source_path")
    src_path = Path(state["source_path"])
    assert src_path.exists()

    source_code = src_path.read_text()

    # Create messages with system prompt and sanitizer information
    messages = [
        SystemMessage(
            content=SANITIZER_SELECTOR_SYSTEM_PROMPT.format(
                cp_name=cp_name, harness_name=harness_name
            )
        ),
    ]
    messages.extend(build_prompts(source_code=source_code))
    messages.append(build_sanitizer_prompt([sanitizer]))

    responses = llm.invoke(messages, cache=False)
    response = responses[-1]

    logger.info(f"Successfully selected sanitizers:\n{response.content}")

    new_state["messages"] = state["messages"] + [HumanMessage(content=response.content)]

    # Update state
    new_state["error"] = {"phase": "sanitizer", "status": "success", "details": ""}

    return new_state
