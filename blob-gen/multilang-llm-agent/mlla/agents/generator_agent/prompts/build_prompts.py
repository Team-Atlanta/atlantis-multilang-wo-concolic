"""Functions for building prompts for the generator agent."""

from pathlib import Path
from typing import List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from mlla.utils import instrument_line
from mlla.utils.code_tags import (
    DIFF_TAG,
    END_DIFF_TAG,
    END_FEEDBACK_TAG,
    END_GENERATOR_CODE_TAG,
    END_GENERATOR_DESC_TAG,
    END_GENERATOR_PLAN_TAG,
    END_PAYLOAD_CODE_TAG,
    END_PAYLOAD_DESC_TAG,
    END_SOURCE_TAG,
    FEEDBACK_TAG,
    FUNCTION_TAG,
    GENERATOR_CODE_TAG,
    GENERATOR_DESC_TAG,
    GENERATOR_PLAN_TAG,
    PAYLOAD_CODE_TAG,
    PAYLOAD_DESC_TAG,
    SOURCE_TAG,
)

from ....modules.known_struct import get_known_struct_prompts
from ....modules.sanitizer import get_exploit_prompt
from ....utils import normalize_func_name

# from ....modules.sanitizer import get_sanitizer_prompt
from ....utils.attribute_cg import (  # get_analysis_report,
    AnnotationOptions,
    AttributeCG,
    AttributeFuncInfo,
)
from .analyze_prompts import GENERATOR_ANALYSIS_PROMPT
from .create_prompts import GENERATOR_CREATION_PROMPT
from .improve_prompts import GENERATOR_IMPROVEMENT_PROMPT
from .plan_prompts import GENERATOR_PLAN_PROMPT
from .sanitizer_prompts import build_sanitizer_sentinel_prompt
from .system_prompts import GENERATOR_SYSTEM_PROMPT

INSTRUCTION_FOR_COMMAND_INJECTION = """
SPECIAL INSTRUCTION FOR COMMAND INJECTION TARGETS:
- If targeting a command injection vulnerability, you MUST use exactly the command "jazze" (no more, no less)
- Do NOT use any other command for command injection exploits
- The command MUST be exactly "jazze" without any arguments, path components, or command chaining
""".strip()  # noqa: E501


SOURCE_PROMPT_WITH_TAG = f"""
{SOURCE_TAG}
The following annotations mark important elements in the code:
- Each function is provided under {FUNCTION_TAG} tags
- <ENTRY_FUNCTION> tags: Mark the entry point function
- <VULNERABLE_FUNCTION> tags: Mark the function containing the vulnerability
- /* @BUG_HERE */ comments: The line immediately after contains the vulnerability
- /* @KEY_CONDITION */ comments: The line immediately after contains an important condition

{{source_code}}
{END_SOURCE_TAG}
""".strip()  # noqa: E501


SOURCE_PROMPT = f"""
{SOURCE_TAG}
{{source_code}}
{END_SOURCE_TAG}
""".strip()  # noqa: E501


DIFF_PROMPT = f"""
* IMPORTANT: Below patch introduced a NEW VULNERABILITY!!! FOCUS ON THIS!!
* Test cases may be an effective target (if available)
* The vulnerability may not be shown in the below, so use your imagination.

{DIFF_TAG}
{{diff_code}}
{END_DIFF_TAG}
""".strip()  # noqa: E501


# Known structure prompt template
KNOWN_STRUCT_PROMPT = """
IMPORTANT: Carefully check the following data structures:

{known_struct_info}
""".strip()  # noqa: E501


# Sanitizer prompt template
SANITIZER_PROMPT = """
This describes the target vulnerability you need to exploit and provides specific instructions.
Your generator must be designed to trigger this vulnerability by following these guidelines:

{sanitizer_info}
""".strip()  # noqa: E501


# Payload information prompt template
PAYLOAD_PROMPT = f"""
Below is information about a previously created payload that you can reference.
Study its structure and approach to inform your generator development:

{PAYLOAD_CODE_TAG}
{{payload_code}}
{END_PAYLOAD_CODE_TAG}

{PAYLOAD_DESC_TAG}
{{payload_desc}}
{END_PAYLOAD_DESC_TAG}
""".strip()  # noqa: E501


ACCUMULATED_CONTEXT = """
Review the following context from previous workflow steps to build upon earlier work.
This information shows the progression of the generator development process:

{accumulated_context}
""".strip()  # noqa: E501


CURRENT_STEP = """
Now focus on the following task instructions for your current workflow step.
This defines exactly what you need to accomplish in this phase of the generator development:

<current_workflow_step>
Current step: {current_step}
</current_workflow_step>

{current_task_prompt}
""".strip()  # noqa: E501


def get_task_specific_prompt(node_type: str):
    """Get the appropriate prompt based on node type."""
    if node_type == "plan":
        task_prompt = GENERATOR_PLAN_PROMPT
    elif node_type == "create":
        task_prompt = GENERATOR_CREATION_PROMPT
    elif node_type == "analyze":
        task_prompt = GENERATOR_ANALYSIS_PROMPT
    elif node_type == "improve":
        task_prompt = GENERATOR_IMPROVEMENT_PROMPT
    else:
        raise ValueError(f"Unknown task type: {node_type}")

    return task_prompt


def get_accumulated_context(
    generator_plan="",
    generator_code="",
    generator_desc="",
    coverage_diff_str="",
    generator_feedback="",
):
    """Build accumulated context from previous steps."""
    context_msg = ""

    if generator_plan:
        context_msg += (
            f"""
{GENERATOR_PLAN_TAG}
{generator_plan}
{END_GENERATOR_PLAN_TAG}
""".strip()
            + "\n\n"
        )

    if generator_desc:
        context_msg += (
            f"""
{GENERATOR_DESC_TAG}
{generator_desc}
{END_GENERATOR_DESC_TAG}
""".strip()
            + "\n\n"
        )

    if generator_code:
        context_msg += (
            f"""
{GENERATOR_CODE_TAG}
{generator_code}
{END_GENERATOR_CODE_TAG}
""".strip()
            + "\n\n"
        )

    if coverage_diff_str:
        context_msg += f"\n{coverage_diff_str}\n\n"

    if generator_feedback:
        context_msg += f"""
{FEEDBACK_TAG}
{generator_feedback}
{END_FEEDBACK_TAG}
""".strip()

    return context_msg.strip()


# Target transition prompt template
TRANSITION_PROMPT = """
<target_destination>
Your goal is to reach the destination function below:
- Entry function: {src_func_name} (starting point)
- Destination function: {dst_func_name} (target function with vulnerability)
</target_destination>
""".strip()  # noqa: E501


def make_harness_prompt(code: str, path: Optional[str] = None) -> str:
    """Format function code with name and path."""
    func_str = "<HARNESS_CODE_INFO>\n"
    if path:
        func_str += f"<FILE_PATH>{path}</FILE_PATH>\n"
    func_str += "<HARNESS_CODE>\n"
    func_str += f"{code}\n"
    func_str += "</HARNESS_CODE>\n"
    func_str += "</HARNESS_CODE_INFO>\n"
    return func_str


def build_prompts(
    add_system: bool = False,
    node_type: str = "",
    src_path: Optional[Path] = None,
    source_code: str = "",
    diff_code: str = "",
    add_exploit_sentinel: bool = False,
    add_known_struct: bool = False,
    attr_cg: Optional[AttributeCG] = None,
    src_func: Optional[AttributeFuncInfo] = None,
    dst_func: Optional[AttributeFuncInfo] = None,
    payload_code: str = "",
    payload_desc: str = "",
    accumulated_context: str = "",
    generator_plan: str = "",
    generator_code: str = "",
    generator_desc: str = "",
    coverage_diff_str: str = "",
    generator_feedback: str = "",
    sanitizers: List[str] = [],
    sanitizer: str = "",
    cp_name: str = "unknown",
    harness_name: str = "unknown",
    add_cmdinjection: bool = False,
) -> List[BaseMessage]:
    """Build a list of messages for a specific node with accumulated context."""
    messages = []

    if add_system:
        # Initialize system message
        sanitizer_name = sanitizers[0] if sanitizers else sanitizer
        system_content = GENERATOR_SYSTEM_PROMPT.format(
            cp_name=cp_name, harness_name=harness_name, sanitizer_name=sanitizer_name
        )
        messages.append(SystemMessage(content=system_content))

    if src_func and dst_func:
        # Add transition information (target destination)
        src_func_name = normalize_func_name(src_func.func_location.func_name)
        dst_func_name = normalize_func_name(dst_func.func_location.func_name)
        transition_msg = TRANSITION_PROMPT.format(
            src_func_name=src_func_name,
            dst_func_name=dst_func_name,
        )
        messages.append(HumanMessage(content=transition_msg))

    source_msg = ""
    if attr_cg:
        # Get source code with annotations
        options = AnnotationOptions(
            show_coverage=True,
            show_bug_location=True,
            show_key_conditions=True,
            show_line_numbers=True,
        )
        attr_cg_str = attr_cg.get_annotated_function_bodies(options)
        source_msg = SOURCE_PROMPT_WITH_TAG.format(source_code=attr_cg_str)
        messages.append(HumanMessage(content=source_msg))

    if source_code:
        source_code = instrument_line(source_code, start_number=1)[0]
        source_msg = make_harness_prompt(source_code, str(src_path))
        # source_msg = SOURCE_PROMPT.format(source_code=source_code)
        messages.append(HumanMessage(content=source_msg))

    if diff_code:
        source_msg = DIFF_PROMPT.format(diff_code=diff_code)
        messages.append(HumanMessage(content=source_msg))

    if add_exploit_sentinel:
        messages.append(build_sanitizer_sentinel_prompt(sanitizers))

    # if attr_cg.bit_node and attr_cg.bit_node.bit_info:
    #     messages.append(HumanMessage(get_analysis_report(attr_cg.bit_node.bit_info)))

    # Add known struct information
    if add_known_struct:
        known_struct_prompts = get_known_struct_prompts(source_code_msg=source_msg)
        if known_struct_prompts:
            struct_msg = KNOWN_STRUCT_PROMPT.format(
                known_struct_info=known_struct_prompts
            )
            messages.append(HumanMessage(content=struct_msg))

    # if add_cmdinjection:
    #     messages.append(HumanMessage(content=INSTRUCTION_FOR_COMMAND_INJECTION))

    # Add sanitizer information (exploit guide)
    if sanitizers and not add_exploit_sentinel:
        # sanitizer_prompt = get_sanitizer_prompt(sanitizers, with_exploit=True)
        sanitizer_prompt = get_exploit_prompt(sanitizers)
        if sanitizer_prompt:
            sanitizer_msg = SANITIZER_PROMPT.format(sanitizer_info=sanitizer_prompt)
            messages.append(HumanMessage(content=sanitizer_msg))

    # # Add payload information if available
    if payload_code or payload_desc:
        payload_msg = PAYLOAD_PROMPT.format(
            payload_code=payload_code if payload_code else "",
            payload_desc=payload_desc if payload_desc else "",
        )
        messages.append(HumanMessage(content=payload_msg))

    # Add accumulated context knowledge
    if (
        generator_plan
        or generator_code
        or generator_desc
        or coverage_diff_str
        or generator_feedback
    ):
        accumulated_context = get_accumulated_context(
            generator_plan,
            generator_code,
            generator_desc,
            coverage_diff_str,
            generator_feedback,
        )

    if accumulated_context:
        accumulated_context_msg = ACCUMULATED_CONTEXT.format(
            accumulated_context=accumulated_context
        )
        messages.append(HumanMessage(content=accumulated_context_msg))

    # Add current step information
    if node_type:
        task_specific_msg = get_task_specific_prompt(node_type)
        if task_specific_msg:
            current_step_msg = CURRENT_STEP.format(
                current_step=node_type.upper(),
                current_task_prompt=task_specific_msg,
            )
            messages.append(HumanMessage(content=current_step_msg))

    return messages


def build_error_msg(errors: List[str]):
    error_msg = "Your generator code produce these errors:\n"
    for idx, e in enumerate(set(errors)):
        error_msg += f"{e}\n\n"

    return error_msg.strip()


def build_error_prompt(errors: List[str]):
    return HumanMessage(content=build_error_msg(errors))
