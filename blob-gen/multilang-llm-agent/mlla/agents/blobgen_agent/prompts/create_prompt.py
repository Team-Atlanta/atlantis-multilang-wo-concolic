# flake8: noqa: E501
from mlla.utils.code_tags import (
    END_PAYLOAD_CODE_TAG,
    END_PAYLOAD_DESC_TAG,
    EXPLOIT_DATA_TAG,
    EXPLOIT_GUIDE_TAG,
    PAYLOAD_CODE_TAG,
    PAYLOAD_DESC_TAG,
)

# Task-specific prompts for different node types
CREATE_PROMPT = f"""
<task>
Implement a Python 'create_payload() -> bytes' function that follows your plan to exploit the known vulnerability:

1. Initial Setup: Create the base payload structure
   - Establish the required format for processing
   - Include necessary headers or markers
   - Set up the foundation for exploitation

2. Vulnerability Exploitation: Craft the exploit component
   - Target the specific vulnerability marked with /* @BUG_HERE */
   - Satisfy all key conditions marked with /* @KEY_CONDITION */
   - Implement precise inputs to trigger the sanitizer

Requirements:
- Return a single bytes object
- Use only built-in Python libraries (e.g., struct, json, base64)
- Handle any necessary loop iterations or state accumulation
- Maintain format validity while exploiting the vulnerability
- Write efficient and effective code with no unnecessary comments
- Avoid any redundant code, variables, or operations
- Document each key condition and how it's satisfied
- Use {EXPLOIT_DATA_TAG} and {EXPLOIT_GUIDE_TAG} effectively
</task>

<methodology>
1. Analyze the code to understand the vulnerability
2. Identify all key conditions that must be satisfied
3. Design a payload structure that maintains format validity
4. Implement precise exploitation techniques
5. Document your approach clearly
</methodology>

<output_format>
1. Document your implementation approach:
{PAYLOAD_DESC_TAG}
- Target vulnerability and trigger mechanism
- Key conditions satisfied
- Data format and endianness considerations
- Expected outcome when the payload is processed
{END_PAYLOAD_DESC_TAG}

2. Provide your implementation:
{PAYLOAD_CODE_TAG}
import struct
# Add other built-in imports as needed

def create_payload() -> bytes:
    \"""Create a payload that exploits the vulnerability.

    Returns:
        bytes: Payload designed to trigger the sanitizer
    \"""
    # Initial Setup: Create base payload structure
    # [Your implementation here]

    # Vulnerability Exploitation: Craft the exploit component
    # [Your implementation here]

    # MUST return only bytes, not tuple/dict
    return final_payload
{END_PAYLOAD_CODE_TAG}
</output_format>
"""


IMPROVE_PROMPT = f"""
<task>
Improve the previous payload based on feedback and analysis to better exploit the vulnerability:

1. Address Identified Issues:
   - Fix problems that prevented successful exploitation
   - Enhance handling of key conditions
   - Refine the approach to trigger the sanitizer

2. Optimize Exploitation Strategy:
   - Improve precision of the exploit component
   - Ensure all format requirements are properly maintained
   - Strengthen the payload's ability to trigger the sanitizer

Requirements:
- Return a single bytes object
- Use only built-in Python libraries
- Address all issues identified in the feedback
- Maintain format validity while exploiting the vulnerability
- Write efficient and effective code with no unnecessary comments
- Avoid any redundant code, variables, or operations
- Document each key condition and how it's satisfied
</task>

<methodology>
1. Review the previous payload and its performance
2. Identify specific issues that prevented successful exploitation
3. Address each issue systematically
4. Refine the exploitation technique
5. Document your improvements clearly
</methodology>

<output_format>
1. Document your implementation approach:
{PAYLOAD_DESC_TAG}
- Target vulnerability and trigger mechanism
- Key conditions satisfied
- Data format and endianness considerations
- Expected outcome when the payload is processed
{END_PAYLOAD_DESC_TAG}

2. Provide your improved implementation:
{PAYLOAD_CODE_TAG}
import struct
# Add other built-in imports as needed

def create_payload() -> bytes:
    \"""Create an improved payload that exploits the vulnerability.

    Returns:
        bytes: Payload designed to trigger the sanitizer
    \"""
    # Initial Setup: Create base payload structure with improvements
    # [Your improved implementation here]

    # Vulnerability Exploitation: Refined exploit component
    # [Your improved implementation here]

    # MUST return only bytes, not tuple/dict
    return final_payload
{END_PAYLOAD_CODE_TAG}
</output_format>
"""


# Payload prompt template
PAYLOAD_PROMPT_TEMPLATE = f"""
Your previous response is shown as below:
- Payload code wrapped with {PAYLOAD_CODE_TAG} tags
- Code description wrapped with {PAYLOAD_DESC_TAG} tags

{PAYLOAD_CODE_TAG}
{{code}}
{END_PAYLOAD_CODE_TAG}

{PAYLOAD_DESC_TAG}
{{desc}}
{END_PAYLOAD_DESC_TAG}
""".strip()


# Script template for payload generation
PAYLOAD_CODE_MAIN = """
import resource

def limit_memory():
    # Limit to 1GB of memory
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, -1))

if __name__ == "__main__":
    # Set memory limit before creating payload
    limit_memory()

    payload = create_payload()
    # Take only first 1MB
    payload = payload[:1024 * 1024]

    with open(sys.argv[1], "wb") as f:
        f.write(payload)
"""
