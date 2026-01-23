"""Prompts for the mutation planning phase."""

from mlla.utils.code_tags import END_MUTATOR_PLAN_TAG, MUTATOR_PLAN_TAG

MUTATION_PLAN_PROMPT = f"""
<task>
Create a detailed mutation plan that will guide the implementation of a mutator function. Your plan should focus on how to:

1. Preserve the data structure until it reaches the source function
   - Understand the source function's input format and processing
   - Identify which parts of the data structure must remain intact

2. Strategically mutate the data after the source point to explore different paths
   - Identify key decision points that affect control flow
   - Determine which mutations would trigger different execution paths

3. Ensure the mutated data can successfully reach and be processed by the destination function
   - Understand the destination function's expected input format
   - Maintain enough format validity for the destination to process it

The goal is to increase code coverage by creating a mutator that preserves structure where needed while introducing strategic mutations to explore different paths.
</task>

<methodology>
Develop your plan by:
1. Analyzing how the source function processes and validates its input
2. Identifying the critical path from source to destination function
3. Mapping key decision points and branches that affect control flow
4. Determining which parts of the data structure to preserve vs. mutate
5. Designing targeted mutation strategies for exploring different paths
</methodology>

<output_format>
Provide your mutation plan within {MUTATOR_PLAN_TAG} tags using the following structure:

{MUTATOR_PLAN_TAG}
# Data Flow Analysis
- Describe the input format of the source function
- Describe the expected input format of the destination function
- Identify the critical path from source to destination
- Explain which parts must be preserved vs. mutated

# Key Mutation Points
- Identify where in the data flow mutations should be applied
- Specify which parts should be preserved until the source point
- Highlight decision points that can be targeted after the source point
- Note any KEY_CONDITION annotations and their significance

# Mutation Strategies
- Outline specific techniques for mutating after the source point
- Describe how to maintain enough validity for the destination function
- Explain strategies for exploring different execution paths
- Include approaches for triggering different branches

# Coverage Goals
- Identify specific code paths to target in the destination function
- Prioritize paths based on their importance and difficulty to reach
- Specify branches and conditions to trigger

# Implementation Approach
- Outline how to parse and preserve the input blob until the source point
- Describe how to apply strategic mutations after the source point
- Explain how to ensure the output can reach the destination function
{END_MUTATOR_PLAN_TAG}
</output_format>
""".strip()  # noqa: E501
