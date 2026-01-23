"""Common utility functions for generator agent nodes."""

import json
import tempfile
from typing import List, Tuple

from loguru import logger

from ....utils.execute_llm_code import WELLKNOWN_LIBS, execute_python_script

# Template for generator execution
GENERATOR_CODE_TEMPLATE = """
import resource
import traceback
import random
from random import Random
{imports}

{generator_code}

def limit_memory():
    # Limit to 1GB of memory
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, -1))

if __name__ == "__main__":
    # Set memory limit
    limit_memory()

    # Create random number generator with fixed seed for reproducibility
    rnd = random.Random({seed})
    results = []

    # Generate multiple payloads
    for i in range({num_blobs}):
        try:
            payload = generate(rnd)

            # Apply size limit
            if len(payload) > 1024 * 1024:  # 1MB limit
                payload = payload[:1024 * 1024]

            results.append({{"status": "success", "payload": payload.hex()}})
        except Exception as e:
            error_msg = traceback.format_exc()
            # Capture error but continue with other blobs
            results.append({{"status": "error", "error": error_msg}})

    # Write all results to output file as JSON
    with open(sys.argv[1], "w") as f:
        json.dump(results, f)
"""


def execute_generator(
    generator_code: str, seed_num: int, num_blobs: int
) -> Tuple[List[bytes], List[str]]:
    """Run generator to produce payloads."""
    # Create complete script
    imports = "\n".join([f"import {x}" for x in WELLKNOWN_LIBS])

    complete_code = GENERATOR_CODE_TEMPLATE.format(
        imports=imports,
        generator_code=generator_code,
        seed=seed_num,
        num_blobs=num_blobs,
    )

    with tempfile.NamedTemporaryFile() as temp_file:
        temp_file_path = temp_file.name
        err = execute_python_script(complete_code, [temp_file_path])

        if err:
            if "MemoryError" in err or "Killed" in err:
                raise ValueError(
                    f"RESOURCE LIMIT ERROR: {err}\n"
                    "- Memory limit exceeded during generation\n"
                    "- Memory limit: 1GB\n"
                    "- Payload size limit: 1MB\n"
                    "Please generate more efficient code."
                )
            raise ValueError(f"Generator execution error:\n{err}")

        # Read the generated results
        with open(temp_file_path, "r") as f:
            blob_results = json.load(f)

            # Extract successful payloads and errors
            payloads = []
            errors = []

            for i, result in enumerate(blob_results):
                if result["status"] == "success":
                    payloads.append(bytes.fromhex(result["payload"]))
                else:
                    # Log the error and collect it
                    logger.warning(f"Blob {i} generation failed: {result['error']}")
                    errors.append(result["error"])

            return payloads, errors
