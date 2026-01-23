"""Prompts for the generator analysis phase."""

# from .tags import GENERATOR_COMPLETED
from mlla.utils.code_tags import COVERAGE_TAG, END_COVERAGE_TAG

# Template for coverage summary
COVERAGE_SUMMARY_TEMPLATE = f"""
* Your generator produced this coverage output:
{COVERAGE_TAG}
Primary Coverage (Functions in target call path):
- Functions: {{filtered_func_count}}
- Files: {{primary_files_count}}
- Lines: {{primary_lines_count}}

Entire Coverage (Including out of call paths):
- Total Functions: {{total_func_count}}
- Total Files: {{total_files_count}}
- Total Lines: {{total_lines_count}}

Changes in Entire Coverage:
- Newly covered: {{new_funcs}} functions in {{new_files_count}} files (+{{new_lines}} lines)
- No longer covered: {{removed_funcs}} functions in {{removed_files_count}} files (-{{removed_lines}} lines)
{END_COVERAGE_TAG}
""".strip()  # noqa: E501

GENERATOR_ANALYSIS_PROMPT = """
<task>
Analyze the coverage results from the generator's output, focusing on both phases of the approach:

Phase 1 Analysis:
- Assess whether the generator successfully reaches the destination function
- Identify which paths were explored while navigating to the destination
- Evaluate the effectiveness of the navigation strategy

Phase 2 Analysis:
- Determine how close the generator came to triggering the vulnerability
- Assess the effectiveness of the exploitation techniques
- Identify which vulnerability conditions were successfully targeted

Overall, evaluate:
- The balance between navigation and exploitation
- Format validity and processing success
- Areas for improvement in both phases

Finally, provide detailed feedback for improvement.
</task>

<methodology>
1. Examine coverage statistics for both navigation and exploitation phases
2. Identify successful paths and unexplored areas
3. Assess proximity to vulnerability triggering
4. Evaluate effectiveness of the two-phase approach
5. Develop targeted improvement suggestions
</methodology>
""".strip()  # noqa: E501
