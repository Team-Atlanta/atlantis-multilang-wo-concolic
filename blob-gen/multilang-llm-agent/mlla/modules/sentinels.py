import base64
import os
import re
import tempfile
from typing import Dict, List, Union

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from ..utils.code_tags import (
    CODE_TAG,
    END_CODE_TAG,
    END_PAYLOAD_DESC_TAG,
    END_SENTINEL_TAG,
    PAYLOAD_DESC_TAG,
    SENTINEL_TAG,
)
from ..utils.execute_llm_code import collect_code_block, execute_python_script
from ..utils.llm import LLM
from .sanitizer import get_sanitizer_prompt

# Messages for sentinel generation and script handling
SENTINEL_GEN_SYSTEM = f"""A sentinel value is a value used during the creation
of an exploit blob to trigger a vulnerability sanitizer. The sanitizer
for you to focus on is `{{sanitizer_name}}`, which you have
previously said is relevant to the codebase. You will also have
access to the relevant source code.

Your task is to generate those sentinel *values* which can actually trigger
the sanitizer to detect vulnerabilities in the code. Please follow the
later to help detect vulnerabilities in the code. Please follow the
following steps when deciding what sentinels you should generate.

<STEP 1>
Extract as much information as about the vulnerability type
from the provided vulnerability sanitizer description. Since
they are short, you are allowed to be creative with your
interpretation of what might inputs trigger such a sanitizer.

Note there may be multiple different values that might be
usable to trigger it. In that case, please remember them and
just output multiple values according to the format described later.

<STEP 2>
Analyze the provided source code and generate some 'structured' values that
might trigger the sanitizer. Do not worry about dataflow, control
flow, or any logical issues yet.

<STEP 3>
Output each sentinel you think is relevant between {SENTINEL_TAG}
and {END_SENTINEL_TAG}. Each sentinel should be in its own set of tags.

Use 'structured' format, i.e., Python expression,
so that your output between {SENTINEL_TAG} and {END_SENTINEL_TAG}
can be directly run in Python.
For example, instead of printing "A" 1000 times and "B" 500 times,
You SHOULD write {SENTINEL_TAG}'A'*1000 + 'b'*500{END_SENTINEL_TAG}.

Note that your output will be interpreted later by other agents, so
so what you need to do is that just ensure the format is
readable and easily understandable, but it should be structured.
-See below python code and check 'YOUR_INPUT':
``` Python
    injection_value = base64.b64decode(YOUR_INPUT)
    injection_value = eval(injection_value)
    if isinstance(injection_value, int):
        injection_value = str(injection_value)

    if isinstance(injection_value, str):
        injection_value = injection_value.encode("utf-8")

    with open(sys.argv[1], "wb") as f:
        f.write(create_payload(injection_value))
```

Output ONLY the strings that would be substituted into the actual exploit to trigger
the sanitizer, so do NOT include any information about variable values, etc in your
sentinel value. Also, DO NOT output any extra characters UNLESS you
explicitly need them, such as newlines or tabs.
"""

# Additional messages for sentinel generation
FUZZER_SPECIFIC_MSG = """Since the project is written in {cp_lang}, remember
the following.

{fuzzer_info}

"""

SENTINEL_SPECIFIC_MSG = """Since the project is written in {cp_lang}, remember
the following sentinel values used by the fuzzer.

{sentinel_info}

"""

SCRIPT_INFO_MSG = """Your input will be base64-encoded and passed
as the second argument to the following script, and thus
you do NOT base64-encode your input as we will handle it for you:

```python
{llm_gen_script}
```
"""

SCRIPT_DESC_MSG = f"""The description of this script:
{PAYLOAD_DESC_TAG}
{{llm_gen_script_desc}}
{END_PAYLOAD_DESC_TAG}

"""

SCRIPT_USAGE_MSG = """You may NOT propose any changes to this script
or assume any changes to it, and you must
assume that your generated sentinel values will be used with the
script to produce final payloads.
"""

SENTINEL_HUMAN_MSG = """Again, the sanitizer to target is `{sanitizer_name}`.
The source code is:
{relevant_code}"""

# Messages for fitting sentinels
FIT_SENTINEL_SYSTEM = f"""A sentinel value is a value used during the creation
of an exploit blob to trigger a vulnerability sanitizer. The sanitizer
for you to focus on is `{{sanitizer_name}}`, which you have
previously said is relevant to the codebase. You will also have
access to the relevant source code.

Your task is to generate some sentinel *values* which will be used
later to help detect vulnerabilities in the code. Please follow the
following steps when deciding what sentinels you should generate.

Your input will be base64-encoded and passed
as the second argument to the following script:
{CODE_TAG}
{{llm_gen_script}}
{END_CODE_TAG}

Thus, do NOT base64-encode your input as we will handle it for
you. Also, if you suggest a change to the sentinel, do NOT output
formatters such as b'' and instead modify the script to adapt to
the input.

Currently, the script gives the error
```
{{err}}
```

Your job is to adjust either the sentinel value or the generation
script so that running it will work, given the initial relevant source
code to find a vulnerability in.

If you would like to modify the sentinel value, then output the new
sentinel value in between the tags {SENTINEL_TAG} and {END_SENTINEL_TAG}.

If you would like to modify the generation script, then output the
new generation script in markdown tags:
```python
<YOUR SCRIPT HERE>
```

You should heavily rely on the provided description of the script to
preserve the script's original intent while making it work with the
sentinel.

Since the current values do not work together, you MUST change at least
one of them to make them work, and possibly both. DO NOT include
any formatters (such as `b''` in Python). I cannot understand bytestrings
such as '\x00', etc. So, if you need to use bytes in the sentinel, modify
the Python script instead.
"""

FIT_SENTINEL_HUMAN_MSG = f"""The source code where there are vulnerabilities is:
{{relevant_code}}

The current generation script is:

```python
{{llm_gen_script}}
```

The script description is:
```
{{llm_gen_script_desc}}
```

The current sentinel is:
{SENTINEL_TAG}
{{sentinel!r}}
{END_SENTINEL_TAG}
"""

SentinelMap = Dict[str, List[Union[str, bytes]]]

JAZZER_SENTINELS: SentinelMap = {
    "FuzzerSecurityIssueCritical: OS Command Injection": [
        os.getenv("JAZZER_COMMAND_INJECTION", "jazze")
    ],
    "FuzzerSecurityIssueMedium: Server Side Request Forgery (SSRF)": [
        os.getenv("JAZZER_SSRF", "jazzer.example.com")
    ],
    "FuzzerSecurityIssueHigh: load arbitrary library": [
        b"\xac\xed\x00\x05sr\x00\x07jaz.Zer\x00\x00\x00\x00\x00\x00\x00"
        b"*\x02\x00\x01B\x00\tsanitizerxp\x02\n"
    ],
    "FuzzerSecurityIssueHigh: SQL Injection": ["'"],
    "FuzzerSecurityIssueCritical: Remote JNDI Lookup": [
        "${jndi:ldap://g.co/}",
        "${ldap://g.co/}",
    ],
    "FuzzerSecurityIssueCritical: LDAP Injection": ["("],
    "FuzzerSecurityIssueHigh: XPath Injection": ["document(2)"],
    "ReflectiveCall": ["jazzer_honeypot"],
    "FuzzerSecurityIssueLow: Regular Expression Injection": ["*"],
    "FuzzerSecurityIssueCritical: Script Engine Injection": ['"jaz"+"zer"'],
    "FuzzerSecurityIssueCritical: File read/write hook path": [
        os.getenv("JAZZER_FILE_READ_WRITE", "jazzer"),
        os.getenv("JAZZER_FILE_SYSTEM_TRAVERSAL_FILE_NAME", "jazzer-traversal"),
    ],
    "FuzzerSecurityIssueCritical: Integer Overflow": [
        "1",
        "0",
        "-1",
        "2147483647",
        "-2147483648",
    ],
}

SUPPORTED_SENTINELS = {"java": JAZZER_SENTINELS, "jvm": JAZZER_SENTINELS}

FUZZER_INFORMATION = {
    "java": (
        """The Java environment is run in Jazzer, a fuzzer for Java applications.
    Here are some basic rules:
    - To demonstrate an OS command injection vulnerability, the final command in the
        exploit should be `jazze`
    - To demonstrate a local file read/write, use the filename `jazzer`
    - To demonstrate request forgery, make external web requests to `jazzer.example.com`
    """
    ),
    "jvm": (
        """The Java environment is run in Jazzer, a fuzzer for Java applications.
    Here are some basic rules:
    - To demonstrate an OS command injection vulnerability, the final command in the
        exploit should be `jazze`
    - To demonstrate a local file read/write, use the filename `jazzer`
    - To demonstrate request forgery, make external web requests to `jazzer.example.com`
    """
    ),
}


def extract_sen_values(text):
    matches = re.findall(
        rf"{SENTINEL_TAG}(?:\r\n|\r|\n)?(.*?)(?:\r\n|\r|\n)?{END_SENTINEL_TAG}",
        text,
        re.DOTALL,
    )

    return matches


def generate_sentinels(
    llm: LLM,
    sanitizer_name: str,
    relevant_code: str,
    llm_gen_script: str = "",
    llm_gen_script_desc: str = "",
    cp_lang: str = "",
) -> List[bytes]:
    system_msg = SENTINEL_GEN_SYSTEM.format(sanitizer_name=sanitizer_name)

    # if cp_lang in FUZZER_INFORMATION:
    #     system_msg += FUZZER_SPECIFIC_MSG.format(
    #         cp_lang=cp_lang, fuzzer_info=FUZZER_INFORMATION[cp_lang]
    #     )

    # if cp_lang in SUPPORTED_SENTINELS:
    #     system_msg += SENTINEL_SPECIFIC_MSG.format(
    #         cp_lang=cp_lang, sentinel_info=SUPPORTED_SENTINELS[cp_lang]
    #     )

    if llm_gen_script:
        system_msg += SCRIPT_INFO_MSG.format(llm_gen_script=llm_gen_script)

        if llm_gen_script_desc:
            system_msg += SCRIPT_DESC_MSG.format(
                llm_gen_script_desc=llm_gen_script_desc
            )

        system_msg += SCRIPT_USAGE_MSG

    def checker(response) -> List[str]:
        content = response.content
        generated_sentinels = extract_sen_values(content)

        if len(generated_sentinels) == 0:
            raise ValueError("No sentinels found.")

        return generated_sentinels

    human_msg = SENTINEL_HUMAN_MSG.format(
        sanitizer_name=sanitizer_name, relevant_code=relevant_code
    )

    messages = [SystemMessage(system_msg), HumanMessage(human_msg)]

    sanitizer_msg = get_sanitizer_prompt(sanitizer_name)
    if sanitizer_msg:
        messages.append(HumanMessage(sanitizer_msg))

    generated_sentinels: List[str] = llm.ask_and_repeat_until(checker, messages, [])

    ret = [i.encode("utf-8") if isinstance(i, str) else i for i in generated_sentinels][
        :20
    ]

    return ret


def fit_sentinels_to_script(
    llm: LLM,
    sanitizer_name: str,
    relevant_code: str,
    llm_gen_script: str,
    llm_gen_script_desc: str,
    sentinel: bytes,
    err: str,
) -> bytes:
    logger.info("enter fit_sentinels_to_script")

    system_msg = FIT_SENTINEL_SYSTEM.format(
        sanitizer_name=sanitizer_name, llm_gen_script=llm_gen_script, err=err
    )

    def checker(response) -> bytes:
        content = response.content
        generated_sentinels = extract_sen_values(content)

        generated_scripts = collect_code_block(content, lang="python")

        if not generated_sentinels and not generated_scripts:
            raise ValueError("No sentinels and generated script found.")

        if not generated_sentinels:
            generated_sentinels.append(sentinel)

        generated_sentinels = [
            i.encode("utf-8") if isinstance(i, str) else i for i in generated_sentinels
        ]

        if not generated_scripts:
            generated_scripts.append(llm_gen_script)

        blobs = []

        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file_path = temp_file.name

            err = execute_python_script(
                generated_scripts[0],
                [temp_file_path, base64.b64encode(generated_sentinels[0]).decode()],
            )

            if err:
                raise ValueError(f"Invalid Python code supplied: {err}")

            with open(temp_file_path, "rb") as f:
                generated_blob = f.read()

                blobs.append(generated_blob)

        if not blobs:
            logger.error("LLM failed to generate sentinel/script")
            return b""

        return blobs[0]

    human_msg = FIT_SENTINEL_HUMAN_MSG.format(
        relevant_code=relevant_code,
        llm_gen_script=llm_gen_script,
        llm_gen_script_desc=llm_gen_script_desc,
        sentinel=sentinel,
    )

    messages = [HumanMessage(system_msg), HumanMessage(human_msg)]

    sanitizer_msg = get_sanitizer_prompt(sanitizer_name)
    if sanitizer_msg:
        messages.append(HumanMessage(sanitizer_msg))

    blob: bytes = llm.ask_and_repeat_until(checker, messages, b"")

    return blob
