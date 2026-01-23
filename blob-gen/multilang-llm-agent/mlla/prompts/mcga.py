"""Prompts for Make Call Graph Agent (MCGA)."""

# Sink Detection Prompts
STEP1_SYSTEM = """You are an Security Vulnerability expert.
We found that the attacker modified the code to cause a security vulnerability,
and we are not sure which function was modified and vulnerable.
Your task is to analyze functions for potential security vulnerabilities where
attacker-controlled data could be exploited.

<analysis_process>
1. Examine the function signature and parameters
2. Trace data flow from parameters through the function
3. Identify potential sink points where data is used in sensitive operations
4. Analyze the context and security implications
</analysis_process>

<sanitizer_description>
{sanitizer_prompt}
</sanitizer_description>

<thinking>
<data_flow_analysis>
- What parameters could be attacker-controlled?
- How is the data processed before reaching sensitive operations?
</data_flow_analysis>

<security_analysis>
- What security checks or validations are present/missing?
- Only consider the security violation that is caught by the given sanitizers.
</security_analysis>


<vulnerability_decision>
- Is there a clear path from attacker input to sensitive operation?
- Can you identify the exact line where the vulnerability exists?
</vulnerability_decision>
</thinking>

<output_format>
{{
    "sink_analysis_message": "Detailed explanation of your findings about the sink",
    "is_vulnerable": true/false,
    "sink_line": "Exact line of code where vulnerability exists"
    "sink_line_number": "Line number of the sink_line"
    "sanitizer_candidates": "A list of sanitizer candidates that
    are triggered by the potential vulnerability.
    If no vulnerability is found, return an empty list."
    "callsites": [{{
        "name": "the function or method Name of callee",
        "tainted_args": [list of indices of tainted arguments of the callee.
        0-indexed. If not tainted, return an empty list],
        "line_range": ((start_row, start_col), (end_row, end_col)), # It is only
        for a single callsite.
        "priority": "set priority of the callee to 0 if it is the most important
        callee to analyze. 1 is the second most important, and so on."
    }},
}}
</output_format>

Return the output_format in JSON format.

<critical_requirements>
- If the code has comment mentioning that it resolves a vulnerability, FOCUS THE
PART OF THE CODE as it may fix the vulnerability improperly, or fake comment by
the attacker.
- If is_vulnerable is true, sink_line MUST contain the exact vulnerable line of code
- If is_vulnerable is false, sink_line should be an empty string, and sink_line_number
should be -1
- sink_line must be copied exactly from the input code without
the line number, no modifications
- Never return is_vulnerable as true without a specific sink_line
- "callsites" must include all the callees that are tainted by the function's
  tainted arguments.
- If all arguments are tainted, "callsites" must include all the callees.
- If the callee's argument is tainted, the return value of the callee is also
tainted, so if other callees use the return value as an argument, they are also
tainted.
- Don't analyze callees recursively. Focus on the current function.
</critical_requirements>"""

# caching friendly prompt
STEP1_SYSTEM_WITH_DIFF = (
    STEP1_SYSTEM
    + """\n
ESPECIALLY, PAY ATTENTION TO THE DIFFS OF THE CODE. THE DIFFS MUST CONTAIN THE
REASON OF THE VULNERABILITY, AS IT WAS MODIFIED BY THE ATTACKER!!!\n"""
    + """
If the diff includes function calls, you must include the callees in the
callsites.
"""
)

STEP1_SYSTEM_WITH_INTERESTING_PARENT = (
    STEP1_SYSTEM
    + """\n
ESPECIALLY, THIS FUNCTION HAS A HIGH PROBABILITY OF BEING VULNERABLE AS ITS
CALLER FUNCTION WAS MODIFIED BY THE ATTACKER."""
)

STEP1_HUMAN = """
{parent_fn_name}
{parent_fn_path}
{parent_fn_body}
<fn_name>
{fn_name}
</fn_name>
<fn_path>
{fn_path}
</fn_path>
<fn_body>
{fn_body}
</fn_body>
<callees>
{callees}
</callees>
<tainted_args>
{tainted_args}
</tainted_args>
"""

# NOT USED
# Step 2: Tainted Callees Analysis
STEP2_HUMAN = """Second step is returning list of callees satisfying the following \
conditions:
1. If the callee's arguments are tainted by the given function's
parameters, then include the callee in the result.
2. Callees can be methods, functions, or any other callable objects,
so if the instance of the method is tainted, include the method in the result.
3. If the tainted callee returns a value, the variable storing the
return value is also tainted so include any other callees that use the
variable as an argument.
4. Any variable assigned by tainted arguments, or tainted variables, are
also tainted, so include any callees that use the variable as an argument.

This condition must be repeatedly checked, which means tracking tainted
variables until you can't find any more tainted callees."""

# NOT USED
# Step 3: Call Graph Construction
STEP3_HUMAN = """The last step is finally returning the call graph.
Each given function must match with a root node of at least one graph.
In the call graph, each node has children fields that must include
nodes representing callee functions.
Each node includes callee function's name, the file path that the
callee is defined in.
Each node has children field that includes the nodes representing
callee functions within the callee function.
If callee is defined in external library, note the library name in the node.
Response includes the result as a list of dictionaries in python.
dictionary format:
{{
    "name": "function_name",
    "file_path": "file_path",
    "lib_name": "library_path",
    "children": [
       dict1, dict2, ...
    ]
}}
"file_path" only includes real file path, not external library path. As the
files are located in target directory, the path must be under the
target directory: {project_dir}.

Use "list_directory" and "file_search" tool to verify if the file path
really exists if not sure."""

STEP3_ERROR = """Your previous answer is wrong.
<ERROR>
{error_msgs}
</ERROR>
Please try again. Remember the path must be under the target directory: \
`{project_dir}`."""

STEP3_PATH_ERROR = " - File path ({path}) does not exist."

# Final Call Graph Node Creation
MAKE_CGNODE_SYSTEM = """You are an experienced software engineer.
<task>
Your task is to generate a call graph from the given function.
</task>
<input>
- Function Name: caller function's name, which is wrapped by <fn_name> tags.
- Callee Analysis Result: callee analysis result for the caller, wrapped by
<callee_analysis_result> tags.
</input>
<output>
Return the root node of the call graph as json object.
format:
{{
    "name": caller function's name,
    "file_path": caller function's file path. If the file path is not in the
    target directory: {project_dir}, or external library, return "".
    "need_to_analyze": need_to_analyze
    "tainted_args": list of indices of tainted arguments of the caller function.
    "start_line": start line of the caller function. If the start line is not
    available, return null.
    "children": [
       dict1, dict2, ...
    ]
}}

<requirements>
- The caller function must match with a root node of the graph.
- If the analysis result contains function names and file paths,
MUST include them in the nodes.
- The root node must have children fields that include nodes representing
callee functions.
- Each node includes callee function's name, the file path that the
callee is defined in.
- If callee is defined in external library, note that in the node.
</requirements>
</output>

Include the json string in the code block wrapped w/ triple backquotes with
json identifier at the beginning of the code block.
"""

MAKE_CGNODE_HUMAN = """
<fn_name>
{fn_name}
</fn_name>
<callee_analysis_result>
{callee_analysis_result}
</callee_analysis_result>
"""
