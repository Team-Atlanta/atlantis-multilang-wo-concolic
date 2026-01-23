# flake8: noqa: E501
"""Common utilities for FuzzedDataProvider implementations."""

import re
from typing import Dict, List, Set, Tuple


def generate_method_mapping(methods_dict: Dict[str, List[Tuple[str, str]]]) -> str:
    """Generate formatted method mapping string from a dictionary of method lists."""
    mapping_lines = ["  <method_mapping>"]
    for base_name, variants in methods_dict.items():
        for consumer, producer in variants:
            mapping_lines.append(f"    {consumer} → {producer}")
    mapping_lines.append("  </method_mapping>")

    return "\n".join(mapping_lines).strip()


def get_base_method_names(methods_dict: Dict[str, List[Tuple[str, str]]]) -> Set[str]:
    """Extract base method names without parameters."""
    # The keys are already the base method names
    return set(methods_dict.keys())


def detect_language(source_code: str) -> str:
    """
    Detect programming language from source code.
    Returns "jvm" for Java/JVM, "llvm" for C/C++, or None.
    """
    # Java/JVM indicators
    java_indicators = [
        r"public\s+class",
        r"import\s+java\.",
        r"public\s+static\s+void\s+fuzzerTestOneInput",
        r"FuzzedDataProvider\s+\w+\s*=",
        r"\w+\.consume[A-Z]",
    ]

    # C/C++ indicators
    cpp_indicators = [
        r"#include",
        r'extern\s+"C"',
        r"LLVMFuzzerTestOneInput",
        r"FuzzedDataProvider\s+\w+\s*\(",
        r"\w+\.Consume[A-Z]",
    ]

    # Count matches
    java_matches = sum(
        1 for pattern in java_indicators if re.search(pattern, source_code)
    )
    cpp_matches = sum(
        1 for pattern in cpp_indicators if re.search(pattern, source_code)
    )

    # Determine language
    if java_matches > cpp_matches:
        return "jvm"
    elif cpp_matches > java_matches:
        return "llvm"

    # Check method patterns if still unclear
    if re.search(r"\w+\.consume[A-Z]", source_code):
        return "jvm"
    elif re.search(r"\w+\.Consume[A-Z]", source_code):
        return "llvm"

    # Let the default be jvm
    return "jvm"


# Example templates for different languages
JAZZER_EXAMPLE = """
  <example>
    <target_code language="java">
      public static void fuzzerTestOneInput(FuzzedDataProvider data) {
          Integer choice = data.consumeInt(1, 6);
          switch (choice) {
              case 4:
                  // our target
                  SearchQueryUtils.getFields(data.consumeRemainingAsString());
                  break;
          }
      }
    </target_code>

    <python_payload>
      import libfdp

      def create_payload():
          # Target value we want to test
          target_value = 4
          internal_payload_str = "field1:value1 field2:value2"

          # Create encoder and add values in the same order as they're consumed
          jazzer_encoder = libfdp.JazzerFdpEncoder()
          jazzer_encoder.produce_jint_in_range(target_value, 1, 6)
          jazzer_encoder.produce_remaining_as_jstring(internal_payload_str)

          # Finalize to get the encoded payload
          final_payload = jazzer_encoder.finalize()
          return final_payload
    </python_payload>
  </example>
"""

LLVM_EXAMPLE = """
  <example>
    <target_code language="cpp">
      extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
          FuzzedDataProvider fdp(data, size);
          int choice = fdp.ConsumeIntegralInRange<int>(0, 3);
          std::string str = fdp.ConsumeRemainingBytesAsString();

          switch (choice) {
              case 0:
                  testMethod1(str);
                  break;
              case 1:
                  testMethod2(str);
                  break;
              default:
                  break;
          }
          return 0;
      }
    </target_code>

    <python_payload>
      import libfdp

      def example_code():
          # Target value we want to test
          target_value = 1
          internal_payload_str = "field1:value1 field2:value2"

          # Create encoder and add values in the same order as they're consumed
          llvm_encoder = libfdp.LlvmFdpEncoder()
          llvm_encoder.produce_int_in_range(target_value, 0, 3)
          llvm_encoder.produce_remaining_bytes_as_string(internal_payload_str)

          # Finalize to get the encoded payload
          final_payload = llvm_encoder.finalize()
          return final_payload
    </python_payload>
  </example>
"""

# Prompt template for FuzzedDataProvider
FDP_PROMPT_TEMPLATE = """
<FuzzedDataProvider>
  <description>
    FuzzedDataProvider is a utility that transforms raw fuzzer input bytes into useful primitive types for fuzzing.
    In Python, the libfdp library allows you to create targeted test inputs by encoding payloads that mimic FuzzedDataProvider behavior.
  </description>

  <core_principles>
    <principle>Analyze target code to identify all FuzzedDataProvider method calls and their exact order</principle>
    <principle>Create a {encoder_type} object to build your payload</principle>
    <principle>Add values in the EXACT SAME ORDER they are consumed in the target code</principle>
    <principle>Call finalize() to get the final encoded payload</principle>
  </core_principles>

  {method_mapping}

  {example}
</FuzzedDataProvider>
""".strip()  # noqa: E501
