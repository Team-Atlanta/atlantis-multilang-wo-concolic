"""Functions for building prompts for the mutator agent."""

from typing import List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from mlla.utils import normalize_func_name
from mlla.utils.attribute_cg import AnnotationOptions, AttributeCG, AttributeFuncInfo
from mlla.utils.code_tags import (
    END_FEEDBACK_TAG,
    END_MUTATOR_CODE_TAG,
    END_MUTATOR_DESC_TAG,
    END_MUTATOR_PLAN_TAG,
    END_SOURCE_TAG,
    FEEDBACK_TAG,
    FUNCTION_TAG,
    MUTATOR_CODE_TAG,
    MUTATOR_DESC_TAG,
    MUTATOR_PLAN_TAG,
    SOURCE_TAG,
)

from ....modules.known_struct import get_known_struct_prompts
from .analyze_prompts import MUTATOR_ANALYSIS_PROMPT
from .create_prompts import MUTATOR_GENERATION_PROMPT
from .improve_prompts import MUTATOR_IMPROVEMENT_PROMPT
from .plan_prompts import MUTATION_PLAN_PROMPT
from .system_prompts import SYSTEM_PROMPT

TRANSITION_PROMPT = """
<target_transition>
Analyze the path between these functions to create an effective mutator:
- Source: {src_func_name} (starting point)
- Destination: {dst_func_name} (target function)
</target_transition>
""".strip()  # noqa: E501


SOURCE_PROMPT = f"""
{SOURCE_TAG}
The following annotations mark important elements in the code:
- Each function is provided under {FUNCTION_TAG} tags
- Entry function is marked with <ENTRY_FUNCTION> tags
- /* @KEY_CONDITION */ comments mark important conditions that affect control flow

{{source_code}}
{END_SOURCE_TAG}
""".strip()  # noqa: E501


# Known structure prompt template
KNOWN_STRUCT_PROMPT = """
IMPORTANT: Carefully check the following data structures:

{known_struct_info}
""".strip()  # noqa: E501


ACCUMULATED_CONTEXT = """
Review the following context from previous workflow steps to build upon earlier work.
This information shows the progression of the mutator development process:

{accumulated_context}
""".strip()  # noqa: E501


# Individual context prompt templates
MUTATOR_PLAN_PROMPT_TEMPLATE = f"""
Strategic plan for this mutation approach:

{MUTATOR_PLAN_TAG}
{{mutator_plan}}
{END_MUTATOR_PLAN_TAG}
""".strip()

MUTATOR_CODE_DESC_PROMPT_TEMPLATE = f"""
Previously created mutator with description and code:

{MUTATOR_DESC_TAG}
{{mutator_desc}}
{END_MUTATOR_DESC_TAG}

{MUTATOR_CODE_TAG}
{{mutator_code}}
{END_MUTATOR_CODE_TAG}
""".strip()

MUTATOR_FEEDBACK_PROMPT_TEMPLATE = f"""
Analysis feedback for improvement:

{FEEDBACK_TAG}
{{mutator_feedback}}
{END_FEEDBACK_TAG}
""".strip()

CURRENT_STEP = """
Now focus on the following task instructions for your current workflow step.
This defines exactly what you need to accomplish in this phase of the mutator development:

<current_workflow_step>
Current step: {current_step}
</current_workflow_step>

{current_task_prompt}
""".strip()  # noqa: E501


# Special instruction for command injection targets
INSTRUCTION_FOR_COMMAND_INJECTION = """
<command_injection>
For command injection vulnerabilities:
- Use ONLY the command "jazze" (no arguments, paths, or chaining)
- No other commands are permitted
</command_injection>
""".strip()  # noqa: E501


def get_task_specific_prompt(node_type: str):
    # Get the appropriate prompt based on node type
    if node_type == "plan":
        task_prompt = MUTATION_PLAN_PROMPT
    elif node_type == "create":
        task_prompt = MUTATOR_GENERATION_PROMPT
    elif node_type == "analyze":
        task_prompt = MUTATOR_ANALYSIS_PROMPT
    elif node_type == "improve":
        task_prompt = MUTATOR_IMPROVEMENT_PROMPT
    else:
        raise ValueError("Task should be given")

    return task_prompt


def get_accumulated_context(mutator_plan, mutator_code, mutator_desc, mutator_feedback):
    context_msg = ""

    if mutator_plan:
        context_msg += (
            f"""
{MUTATOR_PLAN_TAG}
{mutator_plan}
{END_MUTATOR_PLAN_TAG}
""".strip()
            + "\n\n"
        )

    if mutator_desc:
        context_msg += (
            f"""
{MUTATOR_DESC_TAG}
{mutator_desc}
{END_MUTATOR_DESC_TAG}
""".strip()
            + "\n\n"
        )

    if mutator_code:
        context_msg += (
            f"""
{MUTATOR_CODE_TAG}
{mutator_code}
{END_MUTATOR_CODE_TAG}
""".strip()
            + "\n\n"
        )

    if mutator_feedback:
        context_msg += f"""
{FEEDBACK_TAG}
{mutator_feedback}
{END_FEEDBACK_TAG}
""".strip()

    return context_msg.strip()


def build_prompts(
    add_system: bool = False,
    node_type: str = "",
    add_known_struct: bool = False,
    attr_cg: Optional[AttributeCG] = None,
    src_func: Optional[AttributeFuncInfo] = None,
    dst_func: Optional[AttributeFuncInfo] = None,
    mutator_plan: str = "",
    mutator_code: str = "",
    mutator_desc: str = "",
    mutator_feedback: str = "",
) -> List[BaseMessage]:
    """Build a list of messages for a specific node with accumulated context."""
    messages = []

    if add_system:
        # Initialize system message
        messages.append(SystemMessage(content=SYSTEM_PROMPT))

    if src_func and dst_func:
        # Add transition information if available
        src_func_name = normalize_func_name(src_func.func_location.func_name)
        dst_func_name = normalize_func_name(dst_func.func_location.func_name)
        transition_msg = TRANSITION_PROMPT.format(
            src_func_name=src_func_name,
            dst_func_name=dst_func_name,
        )
        messages.append(HumanMessage(content=transition_msg))

    if attr_cg:
        # Get source code
        options = AnnotationOptions(
            show_key_conditions=True,
            show_line_numbers=True,
        )
        attr_cg_msg = attr_cg.get_annotated_function_bodies(options)
        source_msg = SOURCE_PROMPT.format(source_code=attr_cg_msg)
        messages.append(HumanMessage(content=source_msg))

        # Add known struct information
        if add_known_struct:
            known_struct_prompts = get_known_struct_prompts(attr_cg_msg)
            if known_struct_prompts:
                struct_msg = KNOWN_STRUCT_PROMPT.format(
                    known_struct_info=known_struct_prompts
                )
                messages.append(HumanMessage(content=struct_msg))

    # Add individual context components using templates
    if mutator_plan:
        plan_msg = MUTATOR_PLAN_PROMPT_TEMPLATE.format(mutator_plan=mutator_plan)
        messages.append(HumanMessage(content=plan_msg))

    if mutator_code and mutator_desc:
        # Use combined template when both are available
        code_desc_msg = MUTATOR_CODE_DESC_PROMPT_TEMPLATE.format(
            mutator_code=mutator_code, mutator_desc=mutator_desc
        )
        messages.append(HumanMessage(content=code_desc_msg))

    if mutator_feedback:
        feedback_msg = MUTATOR_FEEDBACK_PROMPT_TEMPLATE.format(
            mutator_feedback=mutator_feedback
        )
        messages.append(HumanMessage(content=feedback_msg))

    # Add current step information
    if node_type:
        task_specific_msg = get_task_specific_prompt(node_type)
        current_step_msg = CURRENT_STEP.format(
            current_step=node_type.upper(),
            current_task_prompt=task_specific_msg,
        )
        messages.append(HumanMessage(content=current_step_msg))

    return messages
