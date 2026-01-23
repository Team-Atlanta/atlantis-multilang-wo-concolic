"""Prompts for the generator planning phase."""

GENERATOR_PLAN_PROMPT = """
<task>
Create a detailed plan for a payload generator with two key objectives:
1. PRIMARY: Reach the destination (vulnerable) function through mutation strategies
2. SECONDARY: After reaching destination, exploit the vulnerability

Your plan must address:

1. Path Navigation
   - Analyze the path from entry to destination function
   - Identify obstacles and decision points that make direct access difficult
   - Design strategies to navigate through these obstacles

2. Payload Structure
   - Analyze input format requirements for successful processing
   - Identify which parts must be preserved vs. which can be mutated
   - Understand format constraints that must be maintained

3. Two-Phase Mutation Strategy
   - Phase 1: Mutations to reach the destination function
   - Phase 2: Mutations to exploit the vulnerability once reached
   - Balance between path exploration and targeted exploitation

4. Coverage Optimization
   - Explore paths while attempting to reach the destination
   - After reaching destination, focus on vulnerability exploitation
</task>

<methodology>
1. Analyze the complete path from entry to vulnerable function
2. Identify key decision points that make direct access difficult
3. Design primary mutations to navigate to the destination
4. Design secondary mutations to exploit the vulnerability
5. Balance exploration with targeted exploitation
</methodology>
""".strip()
