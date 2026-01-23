"""Prompts for the generator improvement phase."""

from mlla.utils.code_tags import END_GENERATOR_CODE_TAG, GENERATOR_CODE_TAG

GENERATOR_IMPROVEMENT_PROMPT = f"""
<task>
Improve the generator implementation based on the analysis feedback, focusing on both phases:

Phase 1 Improvements:
- Enhance navigation to the destination function
- Address obstacles identified in the analysis
- Improve path exploration during navigation
- Fix any format validity issues affecting processing

Phase 2 Improvements:
- Carefully check the vulnerability is indeed valid
- Refine exploitation techniques for the vulnerability
- Target specific vulnerability conditions more effectively
- Implement more precise boundary testing
- Optimize exploitation strategies

Maintain the two-phase approach while addressing specific weaknesses identified in the analysis.
Write efficient and effective code with no unnecessary comments or explanations.
Avoid any redundant code, variables, or operations that don't contribute to the goal.
</task>

<methodology>
1. Review the analysis feedback to identify key improvement areas
2. Prioritize changes that will have the greatest impact
3. Enhance both navigation and exploitation phases
4. Maintain format validity and processing success
5. Implement targeted improvements while preserving working functionality
</methodology>

<output_format>
1. Explain your improvements:
- Phase 1 Improvements: Navigation enhancements
- Phase 2 Improvements: Exploitation refinements
- Specific issues addressed from the analysis
- Expected impact on coverage and vulnerability triggering
- Balance between navigation and exploitation

2. Provide your improved implementation:
{GENERATOR_CODE_TAG}
import random
import struct
# Add other built-in imports as needed

def generate(rnd: random.Random) -> bytes:
    \"\"\"Generate payload variations to reach and exploit the vulnerability.

    Args:
        rnd: Random number generator for consistent mutations
    Returns:
        bytes: Payload designed to reach destination and exploit vulnerability
    \"\"\"
    # Phase 1: Navigate to destination function
    # [Your improved implementation here]

    # Phase 2: Exploit vulnerability
    # [Your improved implementation here]

    return final_payload
{END_GENERATOR_CODE_TAG}
</output_format>
""".strip()  # noqa: E501
