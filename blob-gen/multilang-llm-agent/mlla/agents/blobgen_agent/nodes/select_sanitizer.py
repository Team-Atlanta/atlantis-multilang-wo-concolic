import os
import traceback
from functools import partial

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from typing_extensions import Dict, List, Optional

from ....modules.sanitizer import get_sanitizer_prompt, is_known_crash
from ....utils.attribute_cg import AnnotationOptions, AttributeCG
from ....utils.code_tags import SAN_TAG
from ....utils.execute_llm_code import collect_tag
from ....utils.llm import LLM

# Update messages with sanitizer information
from ..prompts.build_prompts import build_prompts
from ..prompts.sanitizer_selector import (
    SANITIZER_SELECTOR_SYSTEM_PROMPT,
    get_sanitizer_example,
)
from ..state import BlobGenAgentOverallState


def verify_sanitizer_selection(
    response, possible_sanitizers_str: str, possible_sanitizers: List[str]
) -> List[str]:
    """Verify that the response contains valid sanitizer selections."""
    content = response.content

    # Extract and validate sanitizer pairs
    try:
        sanitizer_pairs = [
            # Use the last matched one!
            i.strip()
            for i in collect_tag(content, SAN_TAG)[-1].split(",")
        ]
    except (IndexError, AttributeError):
        raise ValueError(f"No {SAN_TAG} tags found")

    if len(sanitizer_pairs) == 0:
        raise ValueError("No vulnerability types found.")

    validated_pairs = []
    for pair in sanitizer_pairs:
        if "." not in pair:
            error_msg = f"Invalid output format: `{pair}`."
            error_msg += "\n\nExpected format: "
            error_msg += "sanitizer_name.vulnerability_type"
            raise ValueError(error_msg)

        sanitizer_name = pair.split(".")[0]
        if sanitizer_name not in possible_sanitizers:
            error_msg = f"Invalid sanitizer suggested: `{sanitizer_name}`."
            error_msg += "\n\nYour choices are:"
            error_msg += f"{possible_sanitizers_str}"
            raise ValueError(error_msg)

        if is_known_crash(pair):
            validated_pairs.append(pair)

    if len(validated_pairs) == 0:
        error_msg = "Select among the given vulnerability types."
        raise ValueError(error_msg)

    return validated_pairs


def generate_sanitizer_selection(
    llm: LLM,
    possible_sanitizers: List[str],
    attr_cg: AttributeCG,
    context: Optional[str] = None,
) -> List[str]:
    """Generate sanitizer selections for the given AttributeCG."""
    possible_sanitizers_str = ", ".join(possible_sanitizers)

    # Create a partial function with the parameters pre-filled
    verification_func = partial(
        verify_sanitizer_selection,
        possible_sanitizers_str=possible_sanitizers_str,
        possible_sanitizers=possible_sanitizers,
    )

    options = AnnotationOptions(
        # show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
        # show_metadata=True,
        # from_leaf=True,
    )
    annotated_code = attr_cg.get_annotated_function_bodies(options)

    system_msg = SANITIZER_SELECTOR_SYSTEM_PROMPT.format(
        sanitizer_name=possible_sanitizers_str,
        output_example=get_sanitizer_example(possible_sanitizers[0]),
    )

    messages = [
        SystemMessage(system_msg),
        HumanMessage(annotated_code),
    ]

    for sanitizer in possible_sanitizers:
        sanitizer_prompt = get_sanitizer_prompt([sanitizer], with_exploit=True)
        messages.append(HumanMessage(sanitizer_prompt))

    if context:
        context = "<additional_context>\n"
        context += f"{context}"
        context += "</additional_context>"
        messages.append(HumanMessage(context))

    # Get max retries from environment variable
    max_retries = int(os.getenv("BGA_MAX_RETRIES", "3"))

    selected_sanitizers: List[str] = llm.ask_and_repeat_until(
        verification_func, messages, [], max_retries=max_retries
    )

    return selected_sanitizers


def select_sanitizer_node(state: BlobGenAgentOverallState) -> Dict:
    """Select appropriate sanitizers for the current context."""
    new_state: Dict = {}
    sanitizer = state["sanitizer"]
    attr_cg = state.get("attr_cg")
    # cg_name = state.get("cg_name", "unknown")
    current_payload = state.get("current_payload", {})

    try:
        assert attr_cg

        # Use LLM to select sanitizers
        logger.info(f"Using LLM to select sanitizers for {attr_cg.name}")
        selected_sanitizers = generate_sanitizer_selection(
            state["llm"],
            [sanitizer],
            attr_cg,
        )

        logger.info(f"Selected sanitizers: {selected_sanitizers} for {attr_cg.name}")

        # Update state with selected sanitizers and success status
        new_state["selected_sanitizers"] = selected_sanitizers
        updated_payload = current_payload.copy()
        updated_payload["selected_sanitizers"] = selected_sanitizers
        new_state["current_payload"] = updated_payload

        # Get existing messages and update with sanitizer information
        messages = state.get("messages", []).copy()

        # Add sanitizer information to messages
        sanitizer_messages = build_prompts(
            sanitizers=selected_sanitizers,
        )
        new_state["messages"] = messages
        new_state["messages"].extend(sanitizer_messages)

        new_state["error"] = {
            "phase": "select_sanitizer",
            "status": "success",
            "details": "",
        }

        # logger.debug(f"Successfully selected sanitizers for CG {cg_name}")

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"Failed to select sanitizers: {error_msg}")

        # Set fallback sanitizer
        new_state["selected_sanitizers"] = [sanitizer]

        # Update error information
        new_state["error"] = {
            "phase": "select_sanitizer",
            "status": "failed",
            "details": error_msg,
        }

    return new_state
