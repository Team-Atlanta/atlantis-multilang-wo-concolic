# flake8: noqa: E501
"""Prompts for the update interesting functions phase."""

from typing import Dict

from langchain_core.messages import HumanMessage

from mlla.utils.code_tags import END_INTERESTING_FUNC_TAG, INTERESTING_FUNC_TAG

FUNCTION_SELECTION_PROMPT = f"""
<task>
Based on the coverage information, select TOP 3 functions whose source code that you want to obtain to maximize coverage expansion and explore unknown code paths.

Focus on functions that are likely to contain unexplored branches, complex logic, or lead to deeper code regions.

By selecting these functions, you will get the source code of those functions provided for further analysis.

IMPORTANT: Only select functions from the coverage data.
</task>

<selection_criteria>
- Functions with multiple execution branches and decision points
- Higher coverage line count (indicates complex logic)
- Functions that process different input formats or types
- Functions that serve as gateways to unexplored code regions
</selection_criteria>

<output_format>
Respond with function names in this exact format:
{INTERESTING_FUNC_TAG}
function1,function2,function3
{END_INTERESTING_FUNC_TAG}

If no interesting functions are found, respond with:
{INTERESTING_FUNC_TAG}
NONE
{END_INTERESTING_FUNC_TAG}
</output_format>
"""

INTERESTING_FUNCTIONS_TEMPLATE = f"""
{INTERESTING_FUNC_TAG}
- Based on the coverage information, you've selected these functions to obtain their source code.
- Lines marked /* @VISITED */ were covered during execution. Use as reference only - may contain inaccuracies. Focus on key conditions to explore more paths.
- We've added additional lines before and after the actual function bodies for better understanding.

{{annotated_sources}}
{END_INTERESTING_FUNC_TAG}
"""


def build_interesting_functions_prompt(function_bodies: Dict[str, str]) -> HumanMessage:
    """Build a prompt containing the interesting function bodies."""
    if not function_bodies:
        return HumanMessage(content="No interesting functions found.")

    # Extract the annotated sources
    annotated_sources = function_bodies.get("annotated_sources", "").strip()

    if not annotated_sources:
        return HumanMessage(
            content="No source code extracted for interesting functions."
        )

    # Use the global template
    prompt_content = INTERESTING_FUNCTIONS_TEMPLATE.format(
        annotated_sources=annotated_sources
    ).strip()

    return HumanMessage(content=prompt_content)
