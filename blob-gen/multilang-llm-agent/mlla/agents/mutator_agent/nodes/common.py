"""Common utilities for mutator agent nodes."""

import os
import tempfile
from typing import Tuple

from langchain_core.messages import AIMessage
from loguru import logger

from mlla.utils.code_tags import MUTATOR_CODE_TAG, MUTATOR_DESC_TAG

from ....utils.execute_llm_code import (
    WELLKNOWN_LIBS,
    collect_code_block,
    collect_tag,
    execute_python_script,
)

# Template for mutator execution
MUTATOR_CODE_TEMPLATE = """
import resource
{imports}

{mutator_code}

def limit_memory():
    # Limit to 1GB of memory
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, -1))

if __name__ == "__main__":
    # Set memory limit
    limit_memory()

    # Create random number generator with fixed seed for reproducibility
    rnd = random.Random({seed})

    # Read input blob
    with open(sys.argv[1], "rb") as f:
        input_blob = f.read()

    # Generate mutated blob
    mutated_blob = mutate(rnd, input_blob)

    # Apply size limit
    if len(mutated_blob) > 1024 * 1024:  # 1MB limit
        mutated_blob = mutated_blob[:1024 * 1024]

    # Write mutated blob
    with open(sys.argv[2], "wb") as f:
        f.write(mutated_blob)
"""


def verify_mutator(response: AIMessage) -> Tuple[str, str]:
    """Verify that the response contains a valid mutator."""
    content = response.content

    # First try to get code from code blocks
    mutator_codes = collect_code_block(content)
    if not mutator_codes:
        mutator_codes = collect_tag(content, MUTATOR_CODE_TAG)
        if not mutator_codes:
            raise ValueError(f"No {MUTATOR_CODE_TAG} found in the response")

    mutator_code = mutator_codes[-1]
    if not mutator_code:
        raise ValueError("Mutator code is empty")

    # Extract the mutator description from the tags
    mutator_descs = collect_tag(content, MUTATOR_DESC_TAG)
    if not mutator_descs:
        raise ValueError(f"No {MUTATOR_DESC_TAG} found in the response")

    mutator_desc = mutator_descs[-1].strip()
    if not mutator_desc:
        raise ValueError("Mutator code description is empty")

    # Verify that the mutator code contains a mutate function
    if "def mutate(rnd: random.Random, seed: bytes)" not in mutator_code:
        raise ValueError(
            "No valid mutate(rnd: random.Random, seed: bytes) "
            "function found in the response"
        )

    # Test the mutator
    logger.debug("Testing mutator with a sample run...")
    seed_num = int(os.getenv("BGA_MUTATOR_SEED_NUM", "31337"))
    test_result = execute_mutator(mutator_code, b"team_atlanta_test_blob", seed_num)
    if not test_result:
        raise ValueError("Mutator test failed")

    return mutator_code, mutator_desc


def execute_mutator(
    mutator_code: str, input_blob: bytes, seed_num: int = 31337
) -> bytes:
    """Run mutator to produce mutated blob."""
    # Create complete script
    imports = "\n".join([f"import {x}" for x in WELLKNOWN_LIBS])

    complete_code = MUTATOR_CODE_TEMPLATE.format(
        imports=imports,
        mutator_code=mutator_code,
        seed=seed_num,
    )

    with (
        tempfile.NamedTemporaryFile() as input_file,
        tempfile.NamedTemporaryFile() as output_file,
    ):
        # Write input blob
        input_file.write(input_blob)
        input_file.flush()

        # Execute mutator
        err = execute_python_script(complete_code, [input_file.name, output_file.name])

        if err:
            if "MemoryError" in err or "Killed" in err:
                raise ValueError(
                    f"RESOURCE LIMIT ERROR: {err}\n"
                    "- Memory limit exceeded during mutation\n"
                    "- Memory limit: 1GB\n"
                    "- Blob size limit: 1MB\n"
                    "Please generate more efficient code."
                )
            raise ValueError(f"Mutator execution error:\n{err}")

        # Read mutated blob
        output_file.seek(0)
        return output_file.read()
