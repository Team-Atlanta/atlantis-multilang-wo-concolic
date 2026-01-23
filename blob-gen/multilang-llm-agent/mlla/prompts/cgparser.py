SELECT_CODE_DICT_SYSTEM = """You are a code review expert.

<task>
Your task is to select the most appropriate function declaration from a list of
search results.
</task>

<input>
The target function name will be wrapped with <Function Name> and </Function
Name> tags.
The snippet of the code that invokes the function we are looking for will be
wrapped with <INVOKE_CODE> and </INVOKE_CODE> tags.
The import statements for the invoke code will be wrapped with <Import
Statements> and </Import Statements> tags.

The list of search results will be wrapped with <SEARCH_RESULTS> and
</SEARCH_RESULTS> tags.
Each search result is wrapped with <SEARCH_RESULT {{idx}}> and
</SEARCH_RESULT {{idx}}> tags.
</input>

<strategy>
YOU ARE SEARCHING FOR THE FUNCTION THAT IS CALLED IN THE INVOKE CODE.
YOU ARE **NOT** SEARCHING FOR THE FUNCTION DEFINED IN THE INVOKE CODE.
If the function name and the function defined in the invoke code are the same,
it's likely not the one we are looking for unless the function is recursive.

If the function we are searching for is a constructor (e.g., class_name.<init>),
the name in the invoke code can be `this` or `super` if the code is Java.

You can find hints from the import statements and the invoke code.
If the search result is only function definition with empty body, it's likely
not the one we are looking for.
Focus on the parameter types in the invoke code, and use the info by comparing
arguments types in the search results.
The number of arguments and return type are also helpful.
</strategy>

<output>
The output must be a json format like below:
{{
    "selected_idx": "The single integer index of the search result that is most
    appropriate. If you cannot find the appropriate function, return -1."
}}
</output>

Include the json string in the code block wrapped w/ triple quotes with
json identifier at the beginning of the code block.
"""

SELECT_CODE_DICT_HUMAN = """
<Function Name>
{func_name}
</Function Name>

<INVOKE_CODE>
{invoke_code}
</INVOKE_CODE>

<Import Statements>
{import_statements}
</Import Statements>

<SEARCH_RESULTS>
{search_results_str}
</SEARCH_RESULTS>
"""

SEARCH_RESULTS_FORMAT = """
<SEARCH_RESULT {idx}>
- Function Name: {func_name}
- File Path: {file_path}
- Function Code: {func_body}
</SEARCH_RESULT {idx}>
"""

SEARCH_RESULTS_FORMAT_WITH_SIGNATURE = """
<SEARCH_RESULT {idx}>
- Function Name: {func_name}
- Function Signature: {func_signature}
- File Path: {file_path}
- Function Code: {func_body}
</SEARCH_RESULT {idx}>
"""
