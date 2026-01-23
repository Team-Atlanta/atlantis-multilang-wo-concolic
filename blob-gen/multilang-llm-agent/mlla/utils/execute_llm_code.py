import os
import re
import subprocess
import sys
import tempfile
import traceback

from loguru import logger

from .code_tags import END_PAYLOAD_DESC_TAG, PAYLOAD_DESC_TAG

WELLKNOWN_LIBS = [
    "base64",
    "struct",
    "sys",
    "hashlib",
    "json",
    "zlib",
    "random",
    "binascii",
    "re",
    "libfdp",
]


def collect_code_block(input: str, lang="python") -> list[str]:
    pattern = rf"```{lang}\n(.*?)```"
    matches = re.findall(pattern, input, re.DOTALL)
    if len(matches) == 0:
        pattern = rf"```{lang}\r\n(.*?)```"
        matches = re.findall(pattern, input, re.DOTALL)
    if len(matches) == 0:
        pattern = rf"```{lang}\r(.*?)```"
        matches = re.findall(pattern, input, re.DOTALL)
    if len(matches) == 0:
        pattern = r"```\n(.*?)```"
        matches = re.findall(pattern, input, re.DOTALL)
    return matches


def collect_blob_desc(input: str) -> list[str]:
    pattern = rf"{PAYLOAD_DESC_TAG}(.*?){END_PAYLOAD_DESC_TAG}"
    matches = re.findall(pattern, input, re.DOTALL)
    return matches


def collect_tag(input: str, tag: str) -> list[str]:
    end_tag = "</" + tag[1:]
    pattern = rf"{tag}(.*?){end_tag}"
    matches = re.findall(pattern, input, re.DOTALL)
    return matches


def execute_python_script(
    content: str, args: list[str] = [], timeout: int = 60, print_log=False
) -> str:
    # Create a temporary file to hold the script content
    with tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="w"
    ) as temp_script:
        temp_script.write(content)
        temp_script.flush()
        temp_script_path = temp_script.name

    try:
        # Execute the temporary script file
        cmd = [sys.executable, temp_script_path] + args
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        # Print output and errors
        if print_log:
            logger.debug(
                "Python script output:\n"
                "- STDOUT:\n"
                f"{res.stdout[:1024]}\n"
                "- STDERR:\n"
                f"{res.stderr[:1024]}"
            )

        if res.returncode == 0:
            return ""

        return res.stderr

    except Exception as e:
        error_msg = f"Error: {e}\n{traceback.format_exc()}"
        return error_msg

    finally:
        # Ensure the temp file is deleted after execution
        if os.path.exists(temp_script_path):
            os.remove(temp_script_path)
