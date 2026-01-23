import os
from typing import Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from mlla.agents.blobgen_agent.state import BlobGenAgentOverallState
from mlla.modules.known_struct import get_known_struct_prompts
from mlla.modules.sanitizer import get_exploit_prompt
from mlla.utils.attribute_cg import (  # , get_analysis_report
    AnnotationOptions,
    AttributeCG,
)
from mlla.utils.code_tags import FEEDBACK_TAG
from mlla.utils.execute_llm_code import collect_tag
from mlla.utils.llm import LLM

from ..prompts.build_prompts import build_prompts
from ..prompts.create_prompt import PAYLOAD_PROMPT_TEMPLATE


def verify_failure_analysis(response: AIMessage) -> str:
    """Extract and verify failure analysis feedback from LLM response."""
    content = response.content

    # Extract the feedback from the tags
    feedback_texts = collect_tag(content, FEEDBACK_TAG)
    if not feedback_texts:
        raise ValueError(f"No {FEEDBACK_TAG} found in the response")

    feedback = feedback_texts[-1].strip()
    if not feedback:
        raise ValueError("Failure analysis feedback is empty")

    logger.debug("Successfully extracted failure analysis feedback")
    return feedback


def analyze_failure(state: BlobGenAgentOverallState) -> Dict:
    """Analyze payload failures using explain_payload_failure."""
    new_state: Dict = {}
    current_payload = state["current_payload"]
    llm = state["llm"]

    max_iter = int(os.getenv("BGA_MAX_ITERATION", "3"))
    logger.info(f"Analyzing payload failure {state['iter_cnt']} / {max_iter}")

    assert current_payload["attr_cg"]

    # Extract run_pov_result data
    run_pov_result = current_payload["run_pov_result"]
    assert run_pov_result

    # Get existing messages and add analysis task prompt
    messages = state["messages"]

    messages.extend(
        build_prompts(
            node_type="analysis",
        )
    )

    # Get max retries from environment variable
    max_retries = int(os.getenv("BGA_MAX_RETRIES", "3"))

    # Use ask_and_repeat_until to ensure we get a valid analysis
    logger.debug("Requesting failure analysis from LLM...")
    feedback = llm.ask_and_repeat_until(
        verify_failure_analysis,
        messages,
        default="",  # Default empty string if all attempts fail
        max_retries=max_retries,
        cache=True,
        cache_index=-2,
    )

    # # Update payload with failure explanation
    # if not responses or not responses[-1]:
    #     error_msg = f"Failed to analyze the failure after {max_retries} attempts."
    #     logger.error(error_msg)
    #     new_state["status"] = "failed"
    #     new_state["error"] = {
    #         "phase": "analysis",
    #         "status": "failed",
    #         "details": error_msg,
    #     }
    #     return new_state

    # response = responses[-1]
    # feedback = response.content
    updated_payload = current_payload.copy()
    updated_payload["failure_explanation"] = feedback
    new_state["current_payload"] = updated_payload

    # Update messages in state with the failure explanation
    new_state["messages"] = messages.copy()
    new_state["messages"].pop()  # failure analysis task prompt
    # new_state["messages"].pop()  # coverage information prompt
    # new_state["messages"].append(response)
    new_state["messages"].extend(build_prompts(failure_explanation=feedback))

    logger.info("Successfully analyzed payload failure")
    new_state["status"] = "success"
    new_state["error"] = {
        "phase": "analysis",
        "status": "success",
        "details": "",
    }

    return new_state
