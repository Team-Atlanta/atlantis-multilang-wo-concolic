"""Prompts for Blob Generation Agent (BGA)."""

from ..utils.code_tags import (
    CODE_TAG,
    COMMAND_DESC_TAG,
    COMMAND_ID_TAG,
    COMMAND_TAG,
    END_BLOBGEN_TAG,
    END_CODE_TAG,
    END_COMMAND_DESC_TAG,
    END_COMMAND_ID_TAG,
    END_COMMAND_TAG,
    END_PAYLOAD_DESC_TAG,
    END_SCENARIO_TAG,
    END_SOURCE_TAG,
    PAYLOAD_DESC_TAG,
    SCENARIO_TAG,
    SOURCE_TAG,
)

# Source Code Format
SOURCE_CODE_FORMAT = f"""
{SOURCE_TAG}
Path: {{file_path}}
Source code:
{CODE_TAG}
{{code}}
{END_CODE_TAG}

Some functions to pay attention to:
{{interesting_functions}}

Some interesting outgoing function calls:
{{outgoing_calls}}
{END_SOURCE_TAG}
"""

# Harness Code Format for Single CG
HARNESS_CODE_SINGLE_CG = f"""
{SOURCE_TAG}
This is the harness code for your payload.
Path: {{harness_path}}
{CODE_TAG}
{{code}}
{END_CODE_TAG}

All this code is related to the root-level function call {{root_name}}.
{END_SOURCE_TAG}
"""

# Harness Code Format for Multiple CGs
HARNESS_CODE_MULTIPLE_CG = f"""
{SOURCE_TAG}
This is the harness code for your payload.
Path: {{harness_path}}
{CODE_TAG}
{{code}}
{END_CODE_TAG}

Functions to pay attention to:
{{root_functions}}
{END_SOURCE_TAG}
"""

# Command Extraction prompt
COMMAND_TEMPLATE = f"""{COMMAND_TAG}
{COMMAND_ID_TAG}
{{identifier}}
{END_COMMAND_ID_TAG}

{COMMAND_DESC_TAG}
{{description}}
{END_COMMAND_DESC_TAG}
{END_COMMAND_TAG}"""

EXAMPLE_COMMAND_1 = COMMAND_TEMPLATE.format(
    identifier="This command is run when the input is equal to 1.",
    description="""This command triggers the foo() function. It modifies
the global state variable `state`.""",
)

EXAMPLE_COMMAND_2 = COMMAND_TEMPLATE.format(
    identifier="This command is run when the input is equal to 2.",
    description="""This command triggers the bar() function. It reads the global state
variable `state`.""",
)

FIND_COMMANDS = (
    """
You are an expert in code analysis. In a fuzzing harness, commands are
specific function calls or actions that execute system logic based on
structured input data. These commands often interact with core components
of the application, modify internal state, or invoke key API functions.
Identifying these commands is essential for analyzing how the harness
processes input.

Example
Consider a generic fuzzing harness:

public void fuzz(byte[] data) throws Exception {
    ByteBuffer buffer = ByteBuffer.wrap(data);
    if (buffer.remaining() < 4) {
        return;
    }

    int commandId = buffer.getInt();
    switch (commandId) {
        case 10:
            processCommandA(buffer);
            break;
        case 20:
            processCommandB(buffer);
            break;
        default:
            break;
    }
}

In this example, the function directs execution to specific
command handlers (processCommandA and processCommandB) based
on the parsed input. These commands trigger meaningful state
changes in the system.

You will be given a harness to analyze. Extract and document the commands
present within it. Commands typically:
1. Modify system state (e.g., updating configurations, incrementing counters).
2. Invoke key API functions (e.g., handling requests, triggering database operations).
3. Parse structured input and decide execution paths accordingly.

When analyzing the provided harness, identify similar patterns where
functions interpret structured input and execute meaningful
actions. Document the extracted commands and their contexts. Specifically,
you must response with a list of general commands that the harness will
be able to trigger, and a summary of what each command does.\n"""
    f"""
Your output must contain an entry marked by {COMMAND_TAG} and
{END_COMMAND_TAG} for each command. Within each entry, you must provide
a textual identifier for the command denoted by {COMMAND_ID_TAG}
and {END_COMMAND_ID_TAG}, which will contain information describing the
command as well as how to trigger the command from the harness. Additionally,
also provide a description of
the command within {COMMAND_DESC_TAG} and {END_COMMAND_DESC_TAG}.

An example for a harness containing two commands is below:
```
{EXAMPLE_COMMAND_1}

{EXAMPLE_COMMAND_2}
```

Follow this format for each command when analyzing the provided harness.
"""
)


# Blob Generation System Prompt
BLOB_GEN_SYSTEM = f"""You must suggest the first parameter that is used in
fuzzerTestOneInput method.
That byte array is to trigger **{{vulntype}}** vulnerabilities in the given code.
I will give you code under the label <Code> in the below.
/*BUG_HERE*/ is located at right before malicious input injection occurs in
the code.
/*BEGIN_KEY_CONDITION*/ and /*END_KEY_CONDITION*/ surround each key condition.
First, I propose STEPs to infer values step by step.


<STEP 1>
The file that is read by fuzzerTestOneInput has at least 1
independent variable.

First, analyze how the input data is processed:
1. Does the harness use a FuzzedDataProvider-like approach or process raw bytes?
   If using FuzzedDataProvider-like approach:
   The input is split into two types of bytes:
   - "data bytes": Complex data that needs parsing (strings, arrays)
     * Placed at and consumed FROM THE BEGINNING of input
     * Examples: strings, arrays, structured data (JSON, PDF)
     * Purpose: Preserve structure of valid input files
     * Methods like consumeRemainingAsString(), consumeBytes()

   - "choice bytes": Small primitives that guide control-flow
     * Placed at and consumed FROM THE END of input
     * Examples: single integers, floats, booleans
     * Purpose: More stable under fuzzer mutations
     * Methods like consumeInt(), consumeBoolean()
     * IMPORTANT:
       - Even if these appear first in the code (like consumeInt()),
         they must still be placed at the END of the input buffer
       - Must carefully handle endianness for numeric values:
         * consumeInt(): 32-bit integer in platform's native byte order
         * consumeLong(): 64-bit integer in platform's native byte order
         * consumeFloat(): 32-bit float in IEEE 754 format
         * consumeDouble(): 64-bit double in IEEE 754 format

   If processing raw bytes:
   - No special organization is needed
   - Generate the byte array based on how the harness processes it

2. What data types are being consumed from the input?
   - Complex data types (strings, arrays, structured data)
   - Simple primitives (integers, floats, booleans)

You MUST answer:
1. How many independent variables are in the byte array
2. How they are deserialized from the byte array
3. The layout of the input data (what goes at beginning vs end)

<STEP 2>
You MUST answer how each independent variable is used.

<STEP 3>
Find /*BUG_HERE*/ in the given code and then you MUST answer which
independent variables are used to inject malicious input. There
may be some important control flow dependencies, which we call
key conditions. You should find /*BEGIN_KEY_CONDITION*/ and
/*END_KEY_CONDITION*/ which denote the beginning and end of a key
condition for you to consider. Use these conditions to reason about
what to put in your input to reach /*BUG_HERE*/

<STEP 4>
Find any hints to construct the payload in an initializer block. This will
help you understand values in field member variables, program
environments such as database records, etc.

<STEP 5>
You MUST answer exact values for all variables to reach the vulnerable
function and inject malicious input to vulnerable function.
When you consider the path to vulnerable function, you MUST infer values
based on instructions that can change control flow such as the conditional
branches and try catch blocks.

<STEP 6>
Write a python script to make a payload to trigger vulnerability as file
whose content will be used the first parameter of fuzzerTestOneInput method.

Check these two requirements:
- First argument of script is a file name.
- Second argument of script will be given further.
- Second argument of script is a value for variables identified in
  <STEP 3>. This will be passed as base64 encoded form.

Fill create_payload() following the rules below:
- Follow the input data organization based on harness type:
  * If harness processes raw bytes:
    - Organize bytes based on how harness processes them
    - No special beginning/end organization needed
    - Follow the platform's default endian (mostly little-endian).

  * If harness uses FuzzedDataProvider-like:
    - For endian, it follows the platform's default (mostly little-endian).
    - "data bytes": Complex data that needs parsing
      * Place FROM THE BEGINNING of input regardless of consumption order
      * Methods: consumeRemainingAsString(), consumeBytes(), etc.
      * Examples: strings, arrays, structured data (JSON, PDF)
      * Purpose: Preserve structure of valid input files

    - "choice bytes": Small primitives that guide control-flow
      * Place FROM THE END of input regardless of consumption order
      * Methods: consumeInt(), consumeBoolean(), consumeFloat(), etc.
      * Examples: single integers, floats, booleans
      * Purpose: More stable under fuzzer mutations
      * NOTE: Even if consumed first (e.g. consumeInt() before consumeString()),
        still place at END of input buffer.

  * If harness uses Java's "ByteBuffer" class:
    - Java's "ByteBuffer" class consider integer values as big-endian.
    - For other Java/C types, it follows the platform's default (mostly little-endian).

- For the example harness:
  ```java
  // Note: FuzzedDataProvider uses the platform's native byte order
  // - We are on the little-endian platform.
  public static void fuzzerTestOneInput(FuzzedDataProvider data) {{{{
    int picker = data.consumeInt();           // consumed first, but place at END
    String input = data.consumeRemainingAsString(); // consumed last, but place at BEGINNING
    ...
  }}}}

  // Note: ByteBuffer handles big-endian for integers
  public static void fuzzerTestOneInput(byte[] data) {{{{
    ByteBuffer buf = ByteBuffer.wrap(data);
    int picker = buf.getInt(); // considers big-endian, although platform is little-endian
    ...
  }}}}
  ```
- Document the layout in comments showing how data is organized and consumed
- Please fill create_payload function in the below code snippet.

```python
import base64
import struct
import sys
import hashlib
import json
import zlib

# =========================================================================
# FOCUS ON THIS PART.
def create_payload(injection_value: bytes) -> bytes:
    # You SHOULD fill this to properly generate and trigger functions in fuzzing harness.
    # You SHOULD consider the data consumption patterns in fuzzing harness (like byte[], FuzzedDataProvider, ByteBuffer, etc).
    # You SHOULD consider the endianness of the data.
    # You SHOULD consider the return type is 'bytes'
    # You SHOULD properly handle the injection_value, which contains Sentinel Values, given by the user.
    # You SHOULD import required libraries here.
    return payload
# =========================================================================

if __name__ == "__main__":
    # DO NOT MODIFY THIS
    injection_value = base64.b64decode(sys.argv[2])
    injection_value = eval(injection_value)
    if isinstance(injection_value, int):
        injection_value = str(injection_value)

    if isinstance(injection_value, str):
        injection_value = injection_value.encode("utf-8")

    with open(sys.argv[1], "wb") as f:
        f.write(create_payload(injection_value))
```



Additionally, provide a description of the payload you created
for future reference. This description should include information such as
which parts of any functions it targets. It must also include the reason
why it will trigger any vulnerability you believe to be present in the code.
Also, make note of the major functions that you believe will be called by
tracing through the execution of your input.
Provide your payload description between {PAYLOAD_DESC_TAG} and
{END_PAYLOAD_DESC_TAG}.

Pay careful attention to how the harness will parse and consume the input data
payload. For example, some code will parse some portions of your input data in a
secondary data format, such as JSON. In these cases, you need to properly
extract and format your input to match required and/or optional fields.

It may be necessary to use multiple commands in your payload if supported by the
provided harness.

Beside a possible sentinel value input, your payload should be fully fleshed
out with no other placeholder values. If you are sure that some values are
unknown, do your best to fill in the blanks.

Prioritize generating a payload to target the most direct vulnerabilities,
in other words the most "obvious" vulnerability payloads, since you will have
multiple chances to generate other payloads later on.

You may need to use muliple commands to trigger the bug. Here are some commands
and their descriptions:

{{commands}}

HANDLED EXCEPTIONS ARE NOT VULNERABILITIES WORTH TARGETING."""  # noqa: E501

# Function List Format
FUNCTION_LIST_FORMAT = """- {name}"""

# Error Messages
INVALID_SCRIPT_ERROR = """Try again.
Invalid Python code supplied: {error}

Your original payload script was:
{code}

Your original payload script description was:
```
{description}
```."""

NO_SANITIZER_ERROR = """Try again.
No generated blobs triggered a sanitizer.

The payload script you used:
<OUTPUT>
{code}
</OUTPUT>

The final blobs generated by runnning your payload:
<PAYLOAD>
{payload_blobs}
</PAYLOAD>

An expert left the following feedback on your generated script:
<FEEDBACK>
{feedback}
</FEEDBACK>
"""

# Scenario Format
SCENARIO_FORMAT = f"{SCENARIO_TAG}{{description}}{END_SCENARIO_TAG}"

# Previous Scenarios Description
SCENARIOS_HISTORY = f"""So far, you have already previously
generated payloads for the following scenarios:
{{scenarios}}

If you believe these scenarios are
a comprehensive list of scenarios that might be relevant to
your task, then reply with the word "{END_BLOBGEN_TAG}".
Otherwise, describe a new scenario that should be explored.
An example of a sufficiently different scenario where you
should keep generator blobs could be one where a different
major function is invoked as a result of your input. You MUST
keep going if you believe that there are more DIFFERENT functions
that you could explore. Consider all independent variables that
you can change in the harness that might affect control flow.
Think carefully through your steps, starting at the fuzzerTestOneInput
method.

You must also consider invoking differing commands, or
multiple commands at the same time if that is possible in the
provided harness.
"""

FOLLOW_SCENARIO_PROMPT = """For this iteration, your payload
should roughly follow the outlined scenario below:

{scenario}
"""
