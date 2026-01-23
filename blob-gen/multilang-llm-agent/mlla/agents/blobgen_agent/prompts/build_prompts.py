from typing import List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from ....modules.known_struct import get_known_struct_prompts
from ....modules.sanitizer import get_exploit_prompt
from ....utils.attribute_cg import AnnotationOptions, AttributeCG
from .create_prompt import CREATE_PROMPT, IMPROVE_PROMPT, PAYLOAD_PROMPT_TEMPLATE
from .failure_analysis import ANALYSIS_PROMPT, ANALYSIS_PROMPT_TEMPLATE
from .system_prompt import PAYLOAD_GEN_SYSTEM_PROMPT

# Error prompt template
ERROR_PROMPT_TEMPLATE = """
An error occurred while processing your previous response:
Error Phase: {error_phase}
Error Description:
{error_desc}
""".strip()


def get_task_prompt(node_type: str) -> str:
    """Get the task-specific prompt based on node type."""
    if node_type == "create":
        return CREATE_PROMPT
    elif node_type == "improve":
        return IMPROVE_PROMPT
    elif node_type == "analysis":
        return ANALYSIS_PROMPT
    else:
        return ""


def build_prompts(
    add_system: bool = False,
    attr_cg: Optional[AttributeCG] = None,
    payload_code: str = "",
    payload_desc: str = "",
    failure_explanation: str = "",
    sanitizers: List[str] = [],
    node_type: str = "",
    cp_name: str = "unknown",
    harness_name: str = "unknown",
) -> List[BaseMessage]:
    """Build a list of messages for the blobgen agent."""
    messages = []

    # Add system message if requested
    if add_system:
        sanitizer_name = sanitizers[0] if sanitizers else "address"
        system_content = PAYLOAD_GEN_SYSTEM_PROMPT.format(
            cp_name=cp_name,
            harness_name=harness_name,
            sanitizer_name=sanitizer_name,
        )
        messages.append(SystemMessage(content=system_content))

    # Add attribute call graph information
    if attr_cg:
        options = AnnotationOptions(
            show_bug_location=True,
            show_key_conditions=True,
            show_line_numbers=True,
            annotation_placement="end",
        )
        attr_cg_msg = attr_cg.get_annotated_function_bodies(options) if attr_cg else ""
        messages.append(HumanMessage(attr_cg_msg))

        # Add known struct information
        known_struct_prompts = get_known_struct_prompts(attr_cg_msg)
        if known_struct_prompts:
            messages.append(HumanMessage(known_struct_prompts))

    # Add sanitizer information
    if sanitizers:
        sanitizer_prompt = get_exploit_prompt(sanitizers)
        if sanitizer_prompt:
            sanitizer_msg = "Follow these exploit instructions:\n"
            sanitizer_msg += sanitizer_prompt
            messages.append(HumanMessage(sanitizer_msg))

    # Add payload information if available
    if payload_code and payload_desc:
        payload_prompt = PAYLOAD_PROMPT_TEMPLATE.format(
            code=payload_code, desc=payload_desc
        )
        messages.append(HumanMessage(content=payload_prompt))

    # Add failure explanation if available
    if failure_explanation:
        analysis_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            failure_explanation=failure_explanation
        )
        messages.append(HumanMessage(content=analysis_prompt))

    # Add task-specific prompt if node_type is provided
    if node_type:
        task_prompt = get_task_prompt(node_type)
        if task_prompt:
            messages.append(HumanMessage(content=task_prompt))

    return messages
