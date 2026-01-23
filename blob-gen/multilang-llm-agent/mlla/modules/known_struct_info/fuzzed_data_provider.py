# flake8: noqa: E501
# Define all method mappings as a dictionary for easier filtering
FUZZED_DATA_PROVIDER_METHODS = {
    "consumeByte(byte min, byte max)": (
        "produce_jbyte_in_range(value: int, min: int, max: int)"
    ),
    "consumeShort(short min, short max)": (
        "produce_jshort_in_range(value: int, min: int, max: int)"
    ),
    "consumeChar(char min, char max)": (
        "produce_jchar_in_range(value: int, min: int, max: int)"
    ),
    "consumeInt(int min, int max)": (
        "produce_jint_in_range(value: int, min: int, max: int)"
    ),
    "consumeLong(long min, long max)": (
        "produce_jlong_in_range(value: int, min: int, max: int)"
    ),
    "consumeByte()": "produce_jbyte(value: int)",
    "consumeShort()": "produce_jshort(value: int)",
    "consumeInt()": "produce_jint(value: int)",
    "consumeLong()": "produce_jlong(value: int)",
    "consumeBoolean()": "produce_jbool(value: bool)",
    "consumeChar()": "produce_jchar(value: int)",
    "consumeCharNoSurrogates()": "produce_jchar(value: int)",
    "consumeProbabilityFloat()": "produce_probability_jfloat(value: float)",
    "consumeProbabilityDouble()": "produce_probability_jdouble(value: float)",
    "consumeRegularFloat(float min, float max)": (
        "produce_regular_jfloat_in_range(value: float, min: float, max: float)"
    ),
    "consumeRegularDouble(double min, double max)": (
        "produce_regular_jdouble_in_range(value: float, min: float, max: float)"
    ),
    "consumeRegularFloat()": "produce_regular_jfloat(value: float)",
    "consumeRegularDouble()": "produce_regular_jdouble(value: float)",
    "consumeFloat()": "produce_jfloat(value: float)",
    "consumeDouble()": "produce_jdouble(value: float)",
    "consumeBooleans(int maxLength)": (
        "produce_jbools(value: List[bool], maxLength: int)"
    ),
    "consumeBytes(int maxLength)": "produce_jbytes(value: bytes, maxLength: int)",
    "consumeShorts(int maxLength)": "produce_jshorts(value: List[int], maxLength: int)",
    "consumeInts(int maxLength)": "produce_jints(value: List[int], maxLength: int)",
    "consumeLongs(int maxLength)": "produce_jlongs(value: List[int], maxLength: int)",
    "consumeRemainingAsBytes()": "produce_remaining_as_jbytes(value: bytes)",
    "consumeAsciiString(int maxLength)": (
        "produce_ascii_string(value: str, maxLength: int)"
    ),
    "consumeRemainingAsAsciiString()": "produce_remaining_as_ascii_string(value: str)",
    "consumeString(int maxLength)": "produce_jstring(value: str, maxLength: int)",
    "consumeRemainingAsString()": "produce_remaining_as_jstring(value: str)",
    "remainingBytes()": "mark_remaining_bytes(value: int)",
    "pickValue(Collection[T] collection)": (
        "produce_picked_value_index_in_jarray(value: int, length: int)"
    ),
    "pickValue(T[] array)": (
        "produce_picked_value_index_in_jarray(value: int, length: int)"
    ),
    "pickValues(Collection[T] collection, int numOfElements)": (
        "produce_picked_value_indexes_in_jarray(value: List[int], length: int)"
    ),
    "pickValues(T[] array, int numOfElements)": (
        "produce_picked_value_indexes_in_jarray(value: List[int], length: int)"
    ),
}


# Function to extract base method names (without parameters) for matching in source code
def get_base_method_names():
    base_names = set()
    for method in FUZZED_DATA_PROVIDER_METHODS.keys():
        # Extract the method name without parameters (everything before the first parenthesis)
        if "(" in method:
            base_name = method.split("(")[0]
            base_names.add(base_name)
        else:
            base_names.add(method)
    return base_names


# Function to generate the method mapping section based on filtered methods
def generate_method_mapping(methods_dict):
    mapping_lines = []
    for consumer, producer in methods_dict.items():
        mapping_lines.append(f"    {consumer} → {producer}")
    return "\n".join(mapping_lines)


# Function to generate the full prompt with filtered methods
def generate_fuzzed_data_provider_prompt(filtered_methods=None):
    # If no filtered methods provided, use all methods
    if filtered_methods is None:
        methods_dict = FUZZED_DATA_PROVIDER_METHODS
    else:
        # Filter the methods dictionary to only include methods whose base names are in the filtered_methods list
        methods_dict = {}
        for k, v in FUZZED_DATA_PROVIDER_METHODS.items():
            base_name = k.split("(")[0] if "(" in k else k
            if base_name in filtered_methods:
                methods_dict[k] = v

    method_mapping_section = generate_method_mapping(methods_dict)

    prompt = (
        """
<FuzzedDataProvider>
  <description>
    FuzzedDataProvider is a utility that transforms raw fuzzer input bytes into useful primitive types for fuzzing.
    In Python, the libfdp library allows you to create targeted test inputs by encoding payloads that mimic FuzzedDataProvider behavior.
  </description>

  <core_principles>
    <principle>Analyze target code to identify all FuzzedDataProvider method calls and their exact order</principle>
    <principle>Create a libfdp.JazzerFdpEncoder() object to build your payload</principle>
    <principle>Add values in the EXACT SAME ORDER they are consumed in the target code</principle>
    <principle>Call finalize() to get the final encoded payload</principle>
  </core_principles>

  <method_mapping>
"""
        + method_mapping_section
        + """
  </method_mapping>

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
</FuzzedDataProvider>
""".strip()
    )  # noqa: E501
    return prompt


# Default prompt with all methods
FUZZED_DATA_PROVIDER_PROMPT = generate_fuzzed_data_provider_prompt()
