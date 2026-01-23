# flake8: noqa: E501
from mlla.utils.code_tags import (
    END_FEEDBACK_TAG,
    FEEDBACK_TAG,
    FUNCTION_TAG,
    PAYLOAD_CODE_TAG,
    PAYLOAD_DESC_TAG,
    SOURCE_TAG,
)

# Analysis prompt template
ANALYSIS_PROMPT_TEMPLATE = f"""
Your previous code failed to trigger the sanitizer. An expert analyzed your failure as below:

{FEEDBACK_TAG}
{{failure_explanation}}
{END_FEEDBACK_TAG}
""".strip()


ANALYSIS_PROMPT = f"""
<task>
Analyze why the payload failed to trigger the vulnerability and provide detailed feedback for improvement:

1. Key conditions to reach the buggy point:
   - Check all @KEY_CONDITION annotations in the code
   - Verify if the execution path reaches the @BUG_HERE annotation
   - Identify which conditions were not satisfied
   - Use the coverage information to see which code paths were executed

2. Endianness issues:
   - Follow the data structure guide for exploit when available
   - Check if byte order (little-endian vs big-endian) is correct

3. Known data structures for exploit:
   - Verify if the payload correctly implements required data structures
   - Check if structure fields are properly initialized
   - Ensure structure sizes and alignments are correct

4. Target vulnerability-specific requirements:
   - Review the specific exploit guide for this vulnerability type
   - Check if the payload follows the required exploitation technique
   - Verify if all necessary components for exploitation are present
   - Note that some exploits require intentional exceptions as in the guide

Requirements:
- Provide specific, actionable feedback for improvement
- Identify concrete issues that prevented successful exploitation
- Suggest precise modifications to address each issue
- Focus on both reaching the vulnerability and triggering the sanitizer
- Avoid any redundant code, variables, or operations
- Provide your analysis between {FEEDBACK_TAG} tags
</task>

<output_format_example>
{FEEDBACK_TAG}
Failure Analysis:
1. Key Conditions Assessment
2. Endianness Issues
3. Data Structure Implementation
4. Vulnerability-Specific Requirements
5. Improvement Recommendations
{END_FEEDBACK_TAG}
</output_format_example>
"""
