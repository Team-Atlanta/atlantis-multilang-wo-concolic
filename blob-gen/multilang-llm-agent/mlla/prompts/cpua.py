"""Prompts for Challenge Problem Understanding Agent (CPUA)."""

# Filter Files Prompts
FILTER_FILES_SYSTEM = """Return a list of possible file extensions in a format that \
Python's eval can understand as a list, for source code files written in \
the provided programming language. Do not enclose it in a code block."""

FILTER_FILES_HUMAN = "Programming Language: {lang}"

# Understand Harnesses Prompts
UNDERSTAND_HARNESSES_SYSTEM = """You are an software security expert.
<term>
- fuzzing harness: a special type of file used to communicate with input
provided by the User.
- entry function: the function, the fuzzing harness started from, that is named
'fuzzerTestOneInput', 'LLVMFuzzerTestOneInput', 'main' or similar, defined in
the fuzzing harness.
- target function: the only functions defined and declared in the target project's
codebase, under ({cp_path}), and invoked directly or indirectly by the fuzzing
harness ({harness_path}). You can consider most callees in the entry function as
target functions, but not functions defined in the harness files and not from standard
libraries.
</term>


<task>
Your task is to find the target functions in the fuzzing harness.

Provide me the name of target functions and the list of functions
from the entry function of the harness to the target functions respecting the output
format.

    <step>
    - First, understand what the code in the fuzzing harness is doing, and its
    security implications.
    - Then, check carefully how the harness is interacting with User's input,
    - Then, figure out all the target functions in the fuzzing harness.
    - Then, figure out the entry function of the fuzzing harness.
    - Finally, find the functions from the entry function of the harness to the
    target functions.
    </step>

    <input>
    The fuzzing harness name will be wrapped with <HARNESS> and </HARNESS> tags.
    The fuzzing harness file content will be wrapped with <CODE> and </CODE> tags.
    </input>

    <output>
    The format must be a json format like below:
    {{
        "function_name": {{
            "entry_to_function": ["function_name", ...],
            "tainted_args": [index, ...],
            "callsite_location": (line_number, column_number),
            "function_path": "file_path",
            "priority": 100,
        }},
        ... (repeat for other APIs)
    }}

    'function_name' is the name of the target function invoked by the harness.

    The value of "function_name" is a dictionary with two keys:
    - "entry_to_function": a list of strings, each string is the name of the
    function invoked by the harness, from the entry function of the harness to the
    target function.
    - "tainted_args": a list of integers, each integer is the index of the tainted
    argument in the target function's arguments.
    - "callsite_location": a tuple of two integers, the line number and the column
    number of the callsite location of the target function.
    - "function_path": the path of the file that defines the target function.
    - "priority": set priority of the target function to 0 if it is the most
    important target function to analyze. 1 is the second most important, and
    so on.
    </output>

    <requirements>
    - target function name must be the exact name of the function invoked by the
    harness, not the full name.
    - Don't include the full name in the target function name such as including
    class or instance name.
    - The harnesses may call multiple functions and the target functions *MUST*
    not be any of the functions defined in the harness files.
    - The target functions *MUST* not be any of the functions defined in other
    projects or standard libraries.
    - The entry function must not be called by any other function in the harness.
    - The entry function must be the only one, it must be included as the first element
    of all the lists.
    - If the function is Java constructor, include class_name.<init> as the
    function name.
    - Don't analyze callees recursively. Focus on the functions in the harness.
    </requirements>
</task>

Include the json output in the code block wrapped w/ triple backquotes with
json identifier at the beginning of the code block.
"""

UNDERSTAND_HARNESSES_FORMAT = """<HARNESS> {name} </HARNESS>
<PATH> {path} </PATH>
<CODE>
{code}
</CODE>"""

UNDERSTAND_HARNESSES_HUMAN = """{harness_info}"""

# get_file_path Prompts
GET_FILE_PATH_SYSTEM = """You are experienced code reviewer.

<terminology>
- fuzzing harness: a special type of file used to communicate with input
provided by the User.
- target function: the only functions defined and declared in the target project's
({cp_name}) codebase, under ({project_dir}), and invoked directly or indirectly by
the fuzzing harness.
</terminology>

<task>
Provide me with the list of tuple of name of the target functions and paths of the
file that defines the target function.

    <step>
    - First, understand what the code in the fuzzing harness is doing.
    - Then, utilize provided tools to find the target functions' definition.
       - As we are using funcion name to find the definition, it is possible that
       the tools return multiple results. Please select the most likely one, considering
       the code in the fuzzing harness.
            - Consider instances, classes, and imports when selecting the most
            liken function.
    </step>

    <input>
    The fuzzing harness name will be wrapped with <HARNESS> and </HARNESS> tags.
    The path of fuzzing harness file will be wrapped with <PATH> and </PATH> tags.
    The list of target functions will be wrapped with <functions> and </functions> tags.
    The fuzzing harness file content will be wrapped with <CODE> and </CODE> tags.
    </input>

    <output>
    The response format must be a json format like below:
    {{
        "harness_name": [["function_name", "file_path"], ...],
        ... (repeat for other harnesses)
    }}

    'harness_name' is the given harness name.

    The value of "harness_name" is a list of list of two strings.
    The first string is the name of the target function.
    The second string is the file path of the file that defines the target function.

    'file_path' only includes real file path.
    The file path must point to a file, not a directory.
    As the files are located in project directory, the path must be under the
    project directory: `{project_dir}`. If the file is not in the project directory,
    the function is not we are looking for.
    </output>

Include the json output in the code block wrapped w/ triple backquotes with
json identifier at the beginning of the code block.
</task>
"""

GET_FILE_PATH_HUMAN = """
{harness_info}"""

GET_FILE_PATH_FORMAT = """
<HARNESS> {name} </HARNESS>
<PATH> {path} </PATH>
<functions> {functions} </functions>
<CODE>
{code}
</CODE>"""

CPUA_ERROR = """Your previous answer was wrong.
The error msg is: "{error}"
Please change your response in accordance with the error msg."""

UNDERSTAND_REFLECTION_SYSTEM = """You are experienced code reviewer.

<terminology>
- target function: the only functions defined and declared in the target
project's ({cp_name}) codebase, under ({project_dir}), and invoked by the fuzzing
harness via reflection.
</terminology>

<task>
Your task is focusing on the reflection in Java, and similar reflection in other
languages.

Therefore, the target functions must be the possible functions that can be
reflected by the reflection APIs.

For example, in the below code,

```java
UserRemoteConfig userRemoteConfig = new UserRemoteConfig();
Method method = UserRemoteConfig.class.getMethod(parts[0], String.class);
method.invoke(userRemoteConfig, parts[1]);
```
the target functions can be any method of `UserRemoteConfig` class. In this case,
you must check the methods of `UserRemoteConfig` class with given tools.

    <step>
    - First, understand what the code in the fuzzing harness is doing.
    - Then, figure out if there is any reflection in the code.
    - If there is reflection, figure out the target functions that can be reflected.
       - To do so, you may need to know what methods are defined in the class.
    - Then, figure out the entry function of the fuzzing harness.
    - Finally, find the functions from the entry function of the harness to the
    target functions, and the index of tainted arguments by user input.
    </step>

    <thinking>
    - What functions looks like they are reflected?
    </thinking>

    <input>
    The fuzzing harness name will be wrapped with <HARNESS> and </HARNESS> tags.
    The fuzzing harness file path will be wrapped with <PATH> and </PATH> tags.
    The fuzzing harness file content will be wrapped with <CODE> and </CODE> tags.
    </input>

    <output>
    Provide me with the name of target functions and the list of functions
    from the entry function of the harness to the target functions, and the
    index of arguments tainted by user input from the fuzzing harness,
    respecting the output format.

    The format must be a json format like below:
    {{
        "function_name": {{
            "entry_to_function": ["function_name", ...],
            "tainted_args": [index, ...],
            "callsite_location": (line_number, column_number),
            "function_path": "file_path",
            "priority": 100,
        }},
        ... (repeat for other functions)
    }}

    'function_name' is the name of the function invoked by the harness via reflection.

    The value of "function_name" is a dictionary with two keys:
    - "entry_to_function": a list of strings, each string is the name of the
    function invoked by the harness, from the entry function of the harness to the
    target function.
    - "tainted_args": a list of integers, each integer is the index of the argument
    tainted by user input from the fuzzing harness in the target function's arguments.
    - "callsite_location": a tuple of two integers, the line number and the column
    number of the callsite location of the target function.
    - "function_path": the path of the file that defines the target function.
    - "priority": set priority of the target function to 0 if it is the most
    important target function to analyze. 1 is the second most important, and
    so on.

    If there is no reflection in the fuzzing harness, please return an empty dictionary.
    </output>

    <requirements>
    - function name must be the exact name of the function invoked by the harness, not
    the full name.
    - Don't include the full name in the function name such as including
    class or instance name.
    - The target functions *MUST* not be any of the functions defined in the
    harness files.
    - The target functions *MUST* not be any of the functions defined in other
    projects or standard libraries.
    - The entry function must not be called by any other function in the harness.
    - The entry function must be the only one, it must be included as the first element
    of all the lists.
    </requirements>
</task>

Include the json output in the code block wrapped w/ triple backquotes with
json identifier at the beginning of the code block.
"""
