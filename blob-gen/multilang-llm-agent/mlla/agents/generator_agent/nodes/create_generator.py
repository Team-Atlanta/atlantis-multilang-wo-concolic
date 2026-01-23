"""Create generator node for the GeneratorAgent workflow."""

import hashlib
import os
from typing import List, Tuple

from langchain_core.messages import AIMessage, BaseMessage
from loguru import logger

from mlla.utils.artifact_storage import store_artifact
from mlla.utils.code_tags import GENERATOR_CODE_TAG
from mlla.utils.execute_llm_code import collect_code_block, collect_tag

from ..prompts import build_prompts
from ..prompts.build_prompts import build_error_msg
from ..state import GeneratorAgentOverallState
from .common import execute_generator


def verify_generator_code(
    response: AIMessage,
) -> Tuple[BaseMessage, Tuple[str, List[bytes]]]:
    """Verify that the response contains a valid generator code and description."""
    content = response.content

    # First try to get code from code blocks
    generator_codes = collect_code_block(content)

    # If no code blocks found, try tags
    if not generator_codes:
        generator_codes = collect_tag(content, GENERATOR_CODE_TAG)
        if not generator_codes:
            raise ValueError(f"No {GENERATOR_CODE_TAG} found")

    # Find the code block containing the generate function
    generator_code = generator_codes[-1]
    if not generator_code:
        raise ValueError("No generator code or description found")

    if "def generate(rnd: random.Random)" not in generator_code:
        raise ValueError("No valid generate(rnd) function found in the response")

    # Get configuration from environment
    seed_num = int(os.getenv("BGA_GENERATOR_SEED_NUM", "31337"))
    num_test_blobs = int(os.getenv("BGA_GENERATOR_NUM_TEST_BLOBS", "3"))

    # Execute the generator once to verify it works
    logger.debug("Testing generator with a sample run...")
    example_blobs, errors = execute_generator(generator_code, seed_num, num_test_blobs)
    if errors:
        error_msg = build_error_msg(errors)
        raise ValueError(f"You must handle these errors:\n{error_msg}")

    logger.debug("Successfully generated and tested the generator function")
    return response, (generator_code, example_blobs)


async def create_generator(
    state: GeneratorAgentOverallState,
) -> GeneratorAgentOverallState:
    """Create initial generator function."""
    # Get configuration from environment
    state = state.copy()
    llm = state["llm"]
    payload = state["payload"]
    max_iter = int(os.getenv("BGA_GENERATOR_MAX_ITERATION", "1"))
    state["iter_cnt"] += 1
    iter_cnt = state["iter_cnt"]

    # Extract function names for logging
    if state.get("src_func") and state.get("dst_func"):
        src_func_name = state["src_func"].func_location.func_name
        dst_func_name = state["dst_func"].func_location.func_name
        sanitizer = ", ".join(state["selected_sanitizers"])

        logger.info(
            f"Creating new generator (iteration {iter_cnt}/{max_iter}) for transition:"
            f" {src_func_name} -> {dst_func_name} [sanitizer: {sanitizer}]"
        )
    else:
        logger.info(
            f"Creating new generator code (iteration {iter_cnt}/{max_iter}) ..."
        )

    # Use ask_and_repeat_until to ensure we get a valid generator plan
    max_retries = int(os.getenv("BGA_GENERATOR_MAX_RETRIES", "3"))

    messages = state["messages"]
    if iter_cnt == 1:
        messages.extend(build_prompts(node_type="create"))
    else:
        messages.extend(build_prompts(node_type="improve"))

    # Use ask_and_repeat_until to ensure we get a valid generator
    result = await llm.aask_and_repeat_until(
        verify_generator_code,
        messages,
        default=None,
        max_retries=max_retries,
        cache=True,
        cache_index=-2,
    )

    if not result:
        error_msg = f"Failed to create generator code after {max_retries} attempts."
        logger.error(error_msg)
        state["error"] = {"phase": "create", "status": "failed", "details": error_msg}
        return state

    response, output = result
    generator_code, example_blobs = output
    if not generator_code or not example_blobs:
        error_msg = f"Failed to create generator code after {max_retries} attempts."
        logger.error(error_msg)
        state["error"] = {"phase": "create", "status": "failed", "details": error_msg}
        return state

    logger.debug(f"Created Generator\nCode:\n{generator_code}")

    # Create and return a GeneratorPayload
    generator_hash = hashlib.md5(generator_code.encode()).hexdigest()

    payload.update(
        {
            "generator_code": generator_code,
            "generator_desc": "",
            "generator_blobs": example_blobs,
            "generator_hash": generator_hash,
        }
    )

    state["payload"] = payload
    state["messages"].pop()  # remove create task
    state["messages"].append(response)

    store_artifact(
        gc=state["gc"],
        agent_name="generator",
        artifact_type="generator",
        artifact_hash=generator_hash,
        artifact_code=generator_code,
        artifact_desc="",
        iter_cnt=state["iter_cnt"],
        src_func=state.get("src_func"),
        dst_func=state.get("dst_func"),
        bit_info=state.get("bit"),
        prompts=state["messages"],
        store_in_output=True,
    )

    # Update state
    state["error"] = {"phase": "generation", "status": "success", "details": ""}

    logger.debug("Successfully generated payload generator")

    return state
