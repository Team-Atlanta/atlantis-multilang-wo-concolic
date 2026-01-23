"""Prompts for the generator creation phase."""

from mlla.utils.code_tags import (
    END_GENERATOR_CODE_TAG,
    EXPLOIT_DATA_TAG,
    EXPLOIT_GUIDE_TAG,
    GENERATOR_CODE_TAG,
)

GENERATOR_CREATION_PROMPT = f"""
<task>
Implement a Python 'generate(rnd: random.Random) -> bytes' function that follows your two-phase plan:

1. Phase 1: Generate payloads that can reach the destination function
   - Navigate through obstacles and decision points
   - Maintain format validity for processing
   - Explore paths while attempting to reach destination

2. Phase 2: Once at destination, create variations to exploit the vulnerability
   - Target specific vulnerability conditions
   - Implement boundary testing and edge cases
   - Focus on triggering the vulnerability

Requirements:
- Use provided Random instance for all randomness
- Return a single bytes object
- Use only built-in Python libraries
- Handle any necessary loop iterations or state accumulation
- Balance format preservation with strategic mutations
- Write efficient and effective code with no unnecessary comments or explanations
- Avoid any redundant code, variables, or operations that don't contribute to the goal
- Use {EXPLOIT_DATA_TAG} and {EXPLOIT_GUIDE_TAG} effectively
</task>

<methodology>
1. Implement the two-phase approach from your plan
2. Create strategies for navigating to the destination
3. Develop exploitation techniques for the vulnerability
4. Balance exploration with targeted exploitation
5. Ensure format validity throughout the process
</methodology>

<output_format>
1. Document your implementation approach:
- Phase 1: Strategy for reaching the destination function
- Phase 2: Approach for exploiting the vulnerability
- Key mutation points and techniques
- Format preservation methods
- Expected variations and their purposes

2. Provide your implementation:
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
    # [Your implementation here]

    # Phase 2: Exploit vulnerability
    # [Your implementation here]

    return final_payload
{END_GENERATOR_CODE_TAG}
</output_format>
""".strip()  # noqa: E501
