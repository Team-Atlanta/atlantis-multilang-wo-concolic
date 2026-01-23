# flake8: noqa: E501
from typing import List

from langchain_core.messages import BaseMessage, HumanMessage

from mlla.utils.attribute_cg import AnnotationOptions, AttributeCG
from mlla.utils.coverage import get_xxd
from mlla.utils.run_pov import RunPovResult

COVERAGE_PROMPT = """
<COVERAGE_INFO_FOR_KEY_CONDITIONS>
<HOW_TO_USE>
Coverage information from payload execution. Use as reference only - may contain inaccuracies. Focus on key conditions and bug locations to guide payload refinement.
</HOW_TO_USE>

<XXD_OUTPUT_FOR_PAYLOAD_BLOB>
{xxd_output}
</XXD_OUTPUT_FOR_PAYLOAD_BLOB>

{coverage_info}
</COVERAGE_INFO_FOR_KEY_CONDITIONS>
""".strip()

STDERR_PROMPT = """
<STDERR_FOR_PAYLOAD_BLOB>
{stderr}
</STDERR_FOR_PAYLOAD_BLOB>
""".strip()

CRASH_PROMPT = """
<CRASH_LOG_FOR_PAYLOAD_BLOB>
{crash_log}
</CRASH_LOG_FOR_PAYLOAD_BLOB>
""".strip()


def build_coverage_prompt(
    attr_cg: AttributeCG, blob: bytes, result: RunPovResult
) -> List[BaseMessage]:
    """Build coverage prompt messages."""
    messages = []

    # Add coverage information
    options = AnnotationOptions(
        show_coverage=True,
        show_bug_location=True,
        show_key_conditions=True,
        # show_should_be_taken_lines=True,
        show_line_numbers=True,
        show_only_annotated_lines=True,
        # annotate_unvisited_mark=True,
        show_func_call_flow=False,
        annotation_placement="end",
    )
    attr_cg_msg = attr_cg.get_annotated_function_bodies(options).strip()
    xxd_output = get_xxd(blob).strip()
    xxd_lines = xxd_output.splitlines()
    num_xxd_lines = len(xxd_lines)
    if num_xxd_lines > 200:
        xxd_output = "\n".join(xxd_lines[:200])
        xxd_output += f"(truncated lines over 200 to {num_xxd_lines})"

    coverage_msg = COVERAGE_PROMPT.format(
        xxd_output=xxd_output, coverage_info=attr_cg_msg
    )

    # Add stderr output if available
    stderr = result.get("stderr", "").strip()
    if stderr:
        coverage_msg += "\n\n" + STDERR_PROMPT.format(stderr=stderr)

    # Add crash log if available
    crash_log = result.get("crash_log", "").strip()
    if crash_log:
        coverage_msg += "\n\n" + CRASH_PROMPT.format(crash_log=crash_log)

    messages.append(HumanMessage(content=coverage_msg.strip()))

    return messages
