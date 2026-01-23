"""Prompts for the mutator creation phase."""

from mlla.utils.code_tags import (
    END_MUTATOR_CODE_TAG,
    END_MUTATOR_DESC_TAG,
    MUTATOR_CODE_TAG,
    MUTATOR_DESC_TAG,
)

MUTATOR_GENERATION_PROMPT = f"""
<task>
Implement a Python 'mutate(rnd: random.Random, seed: bytes) -> bytes' function that:

1. Preserves data structure to reach the source function
2. Introduces strategic mutations to explore paths to the destination function

Your implementation must:
- Parse and analyze the input blob ('seed' parameter, not a random seed)
- Use the provided Random instance for all mutations
- Be self-contained with only built-in Python libraries
- Return a single bytes object that matches the destination format
- Focus on minimal, effective code without unnecessary comments or explanations
- Include only code that directly contributes to achieving the objective

The key goal is to balance structure preservation with mutation:
- Preserve enough structure to reach the source function
- Mutate strategically after that point to explore different paths
- Maintain enough format validity for the destination function
</task>

<methodology>
Follow this approach:

1. Analyze the source and destination formats
2. Identify key mutation points from the plan
3. Implement parsing that preserves essential structure
4. Design targeted mutations for exploring different paths
5. Ensure the output maintains necessary validity
</methodology>

<output_format>
1. Explain your implementation approach:
{MUTATOR_DESC_TAG}
- Explain how your implementation parses and preserves the necessary structure
- Describe the specific mutation strategies you've implemented
- Clarify any important implementation details or design decisions
{END_MUTATOR_DESC_TAG}

2. Provide your implementation:
{MUTATOR_CODE_TAG}
import random
import struct
# Add other built-in imports as needed

def mutate(rnd: random.Random, seed: bytes) -> bytes:
    \"\"\"Generate mutated data to transform between source and destination functions.

    Args:
        rnd: Random number generator for mutations
        seed: Input bytes to parse and transform (not a random seed)

    Returns:
        bytes: Transformed data that can reach the destination function
    \"\"\"
    # Parse the input blob
    # Preserve structure until source point
    # Apply strategic mutations after source point
    # Return the mutated data as bytes
    return transformed_data
{END_MUTATOR_CODE_TAG}
</output_format>
""".strip()  # noqa: E501
