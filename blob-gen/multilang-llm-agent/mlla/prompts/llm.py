from ..utils.code_tags import END_TOOLS_TAG, TOOLS_TAG

TOOL_DESC = (
    f"\nThe tools wrapped with {TOOLS_TAG} and {END_TOOLS_TAG} tags are"
    " available and their priority is written next to the tool"
    " name. High number means higher priority. "
    "If task can be done with multiple tools, the tool with the"
    " highest priority will be used.\n"
)

CONTINUE_MSG = (
    "Your previous answer was truncated. Please continue from the right after the exact"
    " character where you left off. Do not repeat the previous answer, and do not add"
    " any other text."
)

ASK_AND_REPEAT_UNTIL_MSG = (
    "Your previous answer was INVALID.\n"
    "The error was:\n{e}\n\n"
    "Carefully think step by step and try again. "
    "This is the last change.\n\n"
    # "Your response was:\n{response_str}\n\n"
    # "Please change your response in accordance with the error."
)
