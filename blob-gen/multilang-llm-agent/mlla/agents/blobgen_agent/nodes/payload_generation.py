import ast
import hashlib
import os
import tempfile
from typing import Dict, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage
from loguru import logger

from mlla.utils.artifact_storage import store_artifact
from mlla.utils.code_tags import PAYLOAD_CODE_TAG, PAYLOAD_DESC_TAG
from mlla.utils.execute_llm_code import (
    WELLKNOWN_LIBS,
    collect_code_block,
    collect_tag,
    execute_python_script,
)

from ..prompts.build_prompts import build_prompts
from ..prompts.create_prompt import PAYLOAD_CODE_MAIN
from ..state import BlobGenAgentOverallState


def verify_payload_code(
    response: AIMessage,
) -> Tuple[BaseMessage, Tuple[str, str, bytes]]:
    """Verify that the response contains a valid payload code and generate the blob."""
    content = response.content

    # First try to get code from code blocks
    payload_codes = collect_code_block(content)
    if not payload_codes:
        payload_codes = collect_tag(content, PAYLOAD_CODE_TAG)
        if not payload_codes:
            raise ValueError(f"No {PAYLOAD_CODE_TAG} found in the response")

    code = payload_codes[-1]
    if not code:
        raise ValueError("Payload code is empty")

    # Extract the payload description from the tags
    payload_descs = collect_tag(content, PAYLOAD_DESC_TAG)
    if not payload_descs:
        raise ValueError(f"No {PAYLOAD_DESC_TAG} found in the response")

    desc = payload_descs[-1].strip()
    if not desc:
        raise ValueError("Payload description is empty")

    # Verify that the payload code contains a create_payload function
    if "def create_payload()" not in code:
        raise ValueError("No valid create_payload() function found in the response")

    # Test the payload code
    logger.debug("Testing payload code with a sample run...")
    blob = execute_payload_code(code)
    if not blob:
        raise ValueError("Payload generation test failed")

    MAX_BLOB_SIZE = int(
        os.getenv("BGA_MAX_BLOB_SIZE", "1048576")
    )  # 1 MB blob size limit
    if len(blob) > MAX_BLOB_SIZE:
        raise ValueError(
            "Your blob size is too big (> 1MB). Make it more concise and accurate."
        )

    return response, (code, desc, blob)


def execute_payload_code(
    payload_code: str,
) -> Optional[bytes]:
    """Execute payload code to generate a payload blob."""
    # Create complete script with imports and payload code
    imports = "\n".join([f"import {x}" for x in WELLKNOWN_LIBS])

    complete_code = imports + "\n\n" + payload_code + PAYLOAD_CODE_MAIN

    with tempfile.NamedTemporaryFile() as output_file:
        output_file_path = output_file.name

        # Execute payload code
        err = execute_python_script(complete_code, [output_file_path])

        if err:
            if "MemoryError" in err or "Killed" in err:
                raise ValueError(
                    f"RESOURCE LIMIT ERROR: {err}\n"
                    "- Memory limit exceeded during payload generation\n"
                    "- Memory limit: 1GB\n"
                    "- Payload size limit: 1MB\n"
                    "Please generate a more efficient payload."
                )
            raise ValueError(f"Payload code execution error:\n{err}")

        # Read generated blob
        output_file.seek(0)
        return output_file.read()


def extract_function(code: str, func_name: str = "create_payload") -> str | None:
    """Extract function by name."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.unparse(node)
        return None
    except Exception as e:
        logger.error(f"Failed to parse code: {str(e)}")
        return None


def generate_payload(state: BlobGenAgentOverallState) -> Dict:
    """Generate payload code using the payload generation module."""
    new_state: Dict = {}
    # harness_name = state["harness_name"]
    current_payload = state.get("current_payload", {})
    state["iter_cnt"] = state["iter_cnt"] + 1
    iter_cnt = state["iter_cnt"]
    llm = state["llm"]

    max_iter = int(os.getenv("BGA_MAX_ITERATION", "3"))
    logger.info(f"Generating payload code {iter_cnt} / {max_iter}")

    # Use the messages from state
    messages = state["messages"].copy()
    # if iter_cnt == 1:
    #     messages.extend(build_prompts(node_type="create"))
    # else:
    #     messages.extend(build_prompts(node_type="improve"))

    max_retries = int(os.getenv("BGA_MAX_RETRIES", "3"))

    result = llm.ask_and_repeat_until(
        verify_payload_code,
        messages,
        default=None,
        max_retries=max_retries,
        cache=True,
        cache_index=-1,
    )

    if not result:
        error_msg = f"Failed to create payload code after {max_retries} attempts."
        logger.error(error_msg)
        new_state["status"] = "failed"
        new_state["error"] = {
            "phase": "create",
            "status": "failed",
            "details": error_msg,
        }
        return new_state

    response, output = result
    code, desc, blob = output
    if not code or not desc:
        error_msg = f"Failed to create payload code after {max_retries} attempts."
        logger.error(error_msg)
        new_state["status"] = "failed"
        new_state["error"] = {
            "phase": "create",
            "status": "failed",
            "details": error_msg,
        }
        return new_state

    # Create hash and store the blob
    blob_hash = hashlib.md5(blob).hexdigest()

    updated_payload = current_payload.copy()
    updated_payload.update(
        {
            "code": code,
            "desc": desc,
            "blob": blob,
            "blob_hash": blob_hash,
        }
    )

    new_state["messages"] = messages
    # new_state["messages"].append(response)
    new_state["messages"].extend(build_prompts(payload_code=code, payload_desc=desc))

    # new_state["messages"] = messages
    new_state["current_payload"] = updated_payload
    new_state["status"] = "success"
    # Update state
    new_state["error"] = {"phase": "create", "status": "success", "details": ""}
    new_state["iter_cnt"] = state["iter_cnt"]

    # Store the blobs using the new unified artifact storage system
    store_artifact(
        gc=state["gc"],
        agent_name="blobgen",
        artifact_type="blob",
        artifact_hash=blob_hash,
        artifact_code=code,
        artifact_desc=desc,
        artifact_blob=blob,
        iter_cnt=state.get("iter_cnt", 0),
        bit_info=state.get("bit"),
        prompts=state["messages"],
        store_in_output=True,
    )

    return new_state
