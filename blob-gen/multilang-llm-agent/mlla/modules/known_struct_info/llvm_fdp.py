# flake8: noqa: E501
"""LLVM FuzzedDataProvider implementation for C/C++ languages."""

from .fdp import FDP_PROMPT_TEMPLATE, generate_method_mapping
from .fdp import get_base_method_names as get_base_names

# Define method mappings for LLVM (C, C++, etc.)
# fmt: off
LLVM_METHODS = {
    "ConsumeBytes": [
        ("ConsumeBytes(size_t num_bytes)", "produce_bytes(target: bytes, num_bytes: int)")
    ],
    "ConsumeData": [
        ("ConsumeData(void *destination, size_t num_bytes)", "produce_bytes(target: bytes, num_bytes: int)")
    ],
    "ConsumeBytesWithTerminator": [
        ("ConsumeBytesWithTerminator(size_t num_bytes, T terminator)", "produce_bytes_with_terminator(target: bytes, num_bytes: int, terminator: int)")
    ],
    "ConsumeRemainingBytes": [
        ("ConsumeRemainingBytes()", "produce_remaining_bytes(target: bytes)")
    ],
    "ConsumeBytesAsString": [
        ("ConsumeBytesAsString(size_t num_bytes)", "produce_bytes_as_string(target: str, num_bytes: int)")
    ],
    "ConsumeRandomLengthString": [
        ("ConsumeRandomLengthString(size_t max_length)", "produce_random_length_string_with_max_length(target: str, max_length: int)"),
        ("ConsumeRandomLengthString()", "produce_random_length_string(target: str)")
    ],
    "ConsumeRemainingBytesAsString": [
        ("ConsumeRemainingBytesAsString()", "produce_remaining_bytes_as_string(target: str)")
    ],
    "ConsumeIntegral": [
        ("ConsumeIntegral<uint8_t>()", "produce_byte(target: int)"),
        ("ConsumeIntegral<char>()", "produce_char(target: int)"),
        ("ConsumeIntegral<short>()", "produce_short(target: int)"),
        ("ConsumeIntegral<unsigned short>()", "produce_unsigned_short(target: int)"),
        ("ConsumeIntegral<int>()", "produce_int(target: int)"),
        ("ConsumeIntegral<unsigned int>()", "produce_unsigned_int(target: int)"),
        ("ConsumeIntegral<long long>()", "produce_long_long(target: int)"),
        ("ConsumeIntegral<unsigned long long>()", "produce_unsigned_long_long(target: int)")
    ],
    "ConsumeIntegralInRange": [
        ("ConsumeIntegralInRange<uint8_t>(uint8_t min, uint8_t max)", "produce_byte_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<char>(char min, char max)", "produce_char_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<short>(short min, short max)", "produce_short_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<unsigned short>(unsigned short min, unsigned short max)", "produce_unsigned_short_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<int>(int min, int max)", "produce_int_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<unsigned int>(unsigned int min, unsigned int max)", "produce_unsigned_int_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<long long>(long long min, long long max)", "produce_long_long_in_range(target: int, min: int, max: int)"),
        ("ConsumeIntegralInRange<unsigned long long>(unsigned long long min, unsigned long long max)", "produce_unsigned_long_long_in_range(target: int, min: int, max: int)")
    ],
    "ConsumeFloatingPoint": [
        ("ConsumeFloatingPoint<float>()", "produce_float(target: float)"),
        ("ConsumeFloatingPoint<double>()", "produce_double(target: float)")
    ],
    "ConsumeFloatingPointInRange": [
        ("ConsumeFloatingPointInRange<float>(float min, float max)", "produce_float_in_range(target: float, min: float, max: float)"),
        ("ConsumeFloatingPointInRange<double>(double min, double max)", "produce_double_in_range(target: float, min: float, max: float)")
    ],
    "ConsumeProbability": [
        ("ConsumeProbability<float>()", "produce_probability_float(target: float)"),
        ("ConsumeProbability<double>()", "produce_probability_double(target: float)")
    ],
    "ConsumeBool": [
        ("ConsumeBool()", "produce_bool(target: bool)")
    ],
    "ConsumeEnum": [
        ("ConsumeEnum<T>()", "produce_enum(target: int, num_variants: int)")
    ],
    "PickValueInArray": [
        ("PickValueInArray(const T (&array)[size])", "produce_picked_value_index_in_array(target: int, array_size: int)"),
        ("PickValueInArray(const std::array<T, size> &array)", "produce_picked_value_index_in_array(target: int, array_size: int)"),
        ("PickValueInArray(std::initializer_list<const T> list)", "produce_picked_value_index_in_array(target: int, array_size: int)")
    ],
    "remaining_bytes": [
        ("remaining_bytes()", "mark_remaining_bytes(target: int)")
    ]
}
# fmt: on

# Example for LLVM FDP
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

      def create_payload():
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
""".strip()


def get_base_method_names():
    """Get base method names for LLVM methods."""
    return get_base_names(LLVM_METHODS)


def generate_llvm_fdp_prompt(filtered_methods=None):
    """Generate the LLVM FuzzedDataProvider prompt with filtered methods."""
    # If filtered methods are provided, filter the methods dictionary
    if filtered_methods is not None:
        filtered_dict = {}
        for base_name in filtered_methods:
            if base_name in LLVM_METHODS:
                filtered_dict[base_name] = LLVM_METHODS[base_name]
        methods_dict = filtered_dict
    else:
        methods_dict = LLVM_METHODS

    method_mapping_section = generate_method_mapping(methods_dict)

    if method_mapping_section:
        # Fill the template with LLVM-specific values
        prompt = FDP_PROMPT_TEMPLATE.format(
            encoder_type="libfdp.LlvmFdpEncoder()",
            method_mapping=method_mapping_section,
            example=LLVM_EXAMPLE,
        )

    else:
        prompt = ""

    return prompt.strip()


# Default prompt with all methods
LLVM_FDP_PROMPT = generate_llvm_fdp_prompt()
