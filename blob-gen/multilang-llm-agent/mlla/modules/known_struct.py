import re
from typing import Dict, Set

from mlla.utils.code_tags import END_EXPLOIT_DATA_TAG, EXPLOIT_DATA_TAG

from .known_struct_info import (
    JVM_BYTE_BUFFER_PROMPT,
    SERVLET_FILE_UPLOAD_PROMPT,
    generate_jazzer_fdp_prompt,
    generate_llvm_fdp_prompt,
    get_jazzer_method_names,
    get_llvm_method_names,
)
from .known_struct_info.fdp import detect_language

# Tag strings to identify known structures in code
FUZZED_DATA_PROVIDER_TAG = "FuzzedDataProvider"
JVM_BYTE_BUFFER_TAG = "ByteBuffer"
SERVLET_FILE_UPLOAD_TAG = "ServletFileUpload"


def extract_method_calls(source_code: str, class_name: str) -> Set[str]:
    """Extract FDP calls like 'data.consumeInt()'."""
    # Pattern to match both Java-style and C++-style method calls
    # This handles C++ template syntax like ConsumeIntegralInRange<int>
    pattern = r"(?:\w+)\.(\w+)(?:<[^>]*>)?\("
    method_calls = re.findall(pattern, source_code)
    return set(method_calls)


def get_known_struct_prompts(source_code_msg: str = "") -> str:
    """Generate prompts for known data structures with filtered methods."""
    if not source_code_msg:
        return ""

    tag_to_prompt: Dict[str, str] = {
        JVM_BYTE_BUFFER_TAG: JVM_BYTE_BUFFER_PROMPT,
        SERVLET_FILE_UPLOAD_TAG: SERVLET_FILE_UPLOAD_PROMPT,
    }

    prompts = []

    # Handle FuzzedDataProvider
    if FUZZED_DATA_PROVIDER_TAG in source_code_msg:
        # Detect language and extract method calls
        language = detect_language(source_code_msg)
        fdp_methods = extract_method_calls(source_code_msg, FUZZED_DATA_PROVIDER_TAG)
        fdp_prompt = ""
        if fdp_methods:
            # Process based on language
            if language == "llvm":
                all_base_methods = get_llvm_method_names()
                filtered_methods = fdp_methods.intersection(all_base_methods)
                if filtered_methods:
                    fdp_prompt = generate_llvm_fdp_prompt(filtered_methods)

            else:
                # Default to Jazzer if language can't be determined
                all_base_methods = get_jazzer_method_names()
                filtered_methods = fdp_methods.intersection(all_base_methods)
                if filtered_methods:
                    fdp_prompt = generate_jazzer_fdp_prompt(filtered_methods)

            if fdp_prompt:
                prompts.append(fdp_prompt)

    # Add prompts for other tags
    for tag, prompt in tag_to_prompt.items():
        if prompt and tag in source_code_msg:
            prompts.append(prompt)

    # Format the prompts
    output_str = ""
    if prompts:
        output_str += "Follow this guideline "
        output_str += "if you are handling these data structures:\n"
        output_str += f"{EXPLOIT_DATA_TAG}\n"
        output_str += "\n\n".join(prompts) + "\n"
        output_str += f"{END_EXPLOIT_DATA_TAG}"

    return output_str.strip()
