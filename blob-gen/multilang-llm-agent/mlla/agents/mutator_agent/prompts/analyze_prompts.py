"""Prompts for the mutator analysis phase."""

from mlla.utils.code_tags import END_FEEDBACK_TAG, FEEDBACK_TAG, MUTATOR_COMPLETED

MUTATOR_ANALYSIS_PROMPT = f"""
<task>
Evaluate the mutator implementation by comparing it against the mutation plan. Your primary goals are to assess:

1. Whether the mutated output can potentially reach the destination function
2. If the mutator preserves the data structure necessary to reach the source function
3. Whether the code is minimal and focused on achieving the objective

These critical aspects form the foundation of an effective mutator. Focus on:

- How well the mutator preserves essential structure for the source function
- How effectively the mutations explore paths to reach the destination
- Whether the implementation follows the strategies outlined in the plan
- Handling of edge cases and boundary conditions
- Removal of any code that doesn't directly contribute to the objective

If the implementation is complete and effective with no further improvements needed, you may return {MUTATOR_COMPLETED}. Otherwise, provide detailed feedback for improvement.
</task>

<methodology>
Analyze the mutator by:

1. Evaluating source function data preservation
2. Assessing destination function reachability
3. Comparing against the original mutation plan
4. Identifying strengths and weaknesses
5. Developing specific improvement suggestions
</methodology>

<output_format>
Provide your feedback within {FEEDBACK_TAG} tags using the following structure:

{FEEDBACK_TAG}
## Strengths
- [Highlight how well it preserves structure for reaching the source function]
- [Note how effectively it mutates to reach the destination function]
- [Recognize innovative or effective approaches]

## Weaknesses
- [Identify issues with structure preservation or mutation strategy]
- [Point out missed opportunities for better mutations]
- [Highlight any potential bugs or limitations]

## Improvement Suggestions
- [Suggest ways to better preserve structure for the source function]
- [Recommend improved mutation strategies for reaching the destination]
- [Propose specific code improvements]

## Coverage Analysis
- [Assess the balance between structure preservation and mutation]
- [Identify paths that need better coverage]
{END_FEEDBACK_TAG}
</output_format>
""".strip()  # noqa: E501
