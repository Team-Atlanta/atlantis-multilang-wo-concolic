"""Prompts for the mutator improvement phase."""

from mlla.utils.code_tags import (
    END_MUTATOR_CODE_TAG,
    END_MUTATOR_DESC_TAG,
    MUTATOR_CODE_TAG,
    MUTATOR_DESC_TAG,
)

MUTATOR_IMPROVEMENT_PROMPT = f"""
<task>
Improve the provided mutator implementation based on the feedback and analysis. Focus on:

1. Better preserving data structure to reach the source function
   - Address any issues with structure preservation identified in the feedback
   - Enhance the parsing and format handling if needed

2. Improving mutations to better reach the destination function
   - Implement more effective mutation strategies based on the feedback
   - Target key decision points more precisely
   - Enhance exploration of different code paths

3. Addressing specific weaknesses identified in the feedback
   - Fix any bugs or limitations in the current implementation
   - Implement the suggested improvements from the analysis

4. Maintaining code quality and efficiency
   - Focus on minimal, effective code without unnecessary comments or explanations
   - Include only code that directly contributes to achieving the objective
   - Remove any redundant or purely explanatory code
</task>

<methodology>
Follow this approach:

1. Review the feedback to identify specific areas needing improvement
2. Prioritize fixes that address structure preservation and path exploration
3. Implement targeted improvements while maintaining working functionality
4. Ensure your changes directly address the weaknesses identified in the feedback
</methodology>

<output_format>
1. Explain your improvements:
{MUTATOR_DESC_TAG}
- Explain what specific weaknesses from the feedback you addressed
- Describe how your changes improve structure preservation for the source function
- Explain how your changes enhance path exploration to the destination function
- Highlight any other important improvements you made
{END_MUTATOR_DESC_TAG}

2. Provide your improved implementation:
{MUTATOR_CODE_TAG}
def mutate(rnd: random.Random, seed: bytes) -> bytes:
    # Your improved implementation here
    ...
    return mutated_blob
{END_MUTATOR_CODE_TAG}
</output_format>
""".strip()  # noqa: E501
