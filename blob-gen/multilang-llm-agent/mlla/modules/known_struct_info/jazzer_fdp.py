# flake8: noqa: E501
"""Jazzer FuzzedDataProvider implementation for JVM languages."""

from .fdp import FDP_PROMPT_TEMPLATE, generate_method_mapping
from .fdp import get_base_method_names as get_base_names

# Define method mappings for Jazzer (JVM languages)
# fmt: off
JAZZER_METHODS = {
    "consumeByte": [
        ("consumeByte(byte min, byte max)", "produce_jbyte_in_range(target: int, min: int, max: int)"),
        ("consumeByte()", "produce_jbyte(target: int)")
    ],
    "consumeShort": [
        ("consumeShort(short min, short max)", "produce_jshort_in_range(target: int, min: int, max: int)"),
        ("consumeShort()", "produce_jshort(target: int)")
    ],
    "consumeChar": [
        ("consumeChar(char min, char max)", "produce_jchar_in_range(target: str, min: str, max: str)"),
        ("consumeChar()", "produce_jchar(target: str)"),
        ("consumeCharNoSurrogates()", "produce_jchar(target: str)")
    ],
    "consumeInt": [
        ("consumeInt(int min, int max)", "produce_jint_in_range(target: int, min: int, max: int)"),
        ("consumeInt()", "produce_jint(target: int)")
    ],
    "consumeLong": [
        ("consumeLong(long min, long max)", "produce_jlong_in_range(target: int, min: int, max: int)"),
        ("consumeLong()", "produce_jlong(target: int)")
    ],
    "consumeBoolean": [
        ("consumeBoolean()", "produce_jbool(target: bool)")
    ],
    "consumeProbabilityFloat": [
        ("consumeProbabilityFloat()", "produce_probability_jfloat(target: float)")
    ],
    "consumeProbabilityDouble": [
        ("consumeProbabilityDouble()", "produce_probability_jdouble(target: float)")
    ],
    "consumeRegularFloat": [
        ("consumeRegularFloat(float min, float max)", "produce_regular_jfloat_in_range(target: float, min: float, max: float)"),
        ("consumeRegularFloat()", "produce_regular_jfloat(target: float)")
    ],
    "consumeRegularDouble": [
        ("consumeRegularDouble(double min, double max)", "produce_regular_jdouble_in_range(target: float, min: float, max: float)"),
        ("consumeRegularDouble()", "produce_regular_jdouble(target: float)")
    ],
    "consumeFloat": [
        ("consumeFloat()", "produce_jfloat(target: float)")
    ],
    "consumeDouble": [
        ("consumeDouble()", "produce_jdouble(target: float)")
    ],
    "consumeBooleans": [
        ("consumeBooleans(int maxLength)", "produce_jbools(target: List[bool], maxLength: int)")
    ],
    "consumeBytes": [
        ("consumeBytes(int maxLength)", "produce_jbytes(target: bytes, maxLength: int)")
    ],
    "consumeShorts": [
        ("consumeShorts(int maxLength)", "produce_jshorts(target: List[int], maxLength: int)")
    ],
    "consumeInts": [
        ("consumeInts(int maxLength)", "produce_jints(target: List[int], maxLength: int)")
    ],
    "consumeLongs": [
        ("consumeLongs(int maxLength)", "produce_jlongs(target: List[int], maxLength: int)")
    ],
    "consumeRemainingAsBytes": [
        ("consumeRemainingAsBytes()", "produce_remaining_as_jbytes(target: bytes)")
    ],
    "consumeAsciiString": [
        ("consumeAsciiString(int maxLength)", "produce_ascii_string(target: str, maxLength: int)")
    ],
    "consumeRemainingAsAsciiString": [
        ("consumeRemainingAsAsciiString()", "produce_remaining_as_ascii_string(target: str)")
    ],
    "consumeString": [
        ("consumeString(int maxLength)", "produce_jstring(target: str, maxLength: int)")
    ],
    "consumeRemainingAsString": [
        ("consumeRemainingAsString()", "produce_remaining_as_jstring(target: str)")
    ],
    "remainingBytes": [
        ("remainingBytes()", "mark_remaining_bytes(target: bytes)")
    ],
    "pickValue": [
        ("pickValue(Collection[T] collection)", "produce_picked_value_index_in_jarray(target: int, collection: Collection[T])"),
        ("pickValue(T[] array)", "produce_picked_value_index_in_jarray(target: int, array: List[T])")
    ],
    "pickValues": [
        ("pickValues(Collection[T] collection, int numOfElements)", "produce_picked_value_indexes_in_jarray(target: List[int], collection: Collection[T], numOfElements: int)"),
        ("pickValues(T[] array, int numOfElements)", "produce_picked_value_indexes_in_jarray(target: List[int], array: List[T], numOfElements: int)")
    ]
}
# fmt: on

# Example for Jazzer FDP
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
""".strip()


def get_base_method_names():
    """Get base method names for Jazzer methods."""
    return get_base_names(JAZZER_METHODS)


def generate_jazzer_fdp_prompt(filtered_methods=None):
    """Generate the Jazzer FuzzedDataProvider prompt with filtered methods."""
    # If filtered methods are provided, filter the methods dictionary
    if filtered_methods is not None:
        filtered_dict = {}
        for base_name in filtered_methods:
            if base_name in JAZZER_METHODS:
                filtered_dict[base_name] = JAZZER_METHODS[base_name]
        methods_dict = filtered_dict
    else:
        methods_dict = JAZZER_METHODS

    method_mapping_section = generate_method_mapping(methods_dict)

    if method_mapping_section:
        # Fill the template with Jazzer-specific values
        prompt = FDP_PROMPT_TEMPLATE.format(
            encoder_type="libfdp.JazzerFdpEncoder()",
            method_mapping=method_mapping_section,
            example=JAZZER_EXAMPLE,
        )

    else:
        prompt = ""

    return prompt.strip()


# Default prompt with all methods
JAZZER_FDP_PROMPT = generate_jazzer_fdp_prompt()
