# flake8: noqa: E501
FUZZED_DATA_PROVIDER_PROMPT = """
<FuzzedDataProvider>
  <description>
    FuzzedDataProvider is a utility that transforms raw fuzzer input bytes into useful primitive types (strings, integers, arrays) for fuzzing.
  </description>

  <core_principles>
    <principle>Control data (integers, booleans) consumed from the END of the buffer</principle>
    <principle>Complex data (strings, arrays) consumed from the BEGINNING of the buffer</principle>
    <principle>All numeric values are handled in LITTLE-ENDIAN format, even though they're fetched from the end</principle>
  </core_principles>

  <methods>
    <from_end>
      <java>
        <method>consumeInt()</method>
        <method>consumeInt(min, max)</method>
        <method>consumeBool()</method>
        <method>consumeLong()</method>
        <method>consumeFloat()</method>
        <method>consumeDouble()</method>
      </java>
      <cpp>
        <method>ConsumeIntegral<T>()</method>
        <method>ConsumeIntegralInRange<T>(min, max)</method>
        <method>ConsumeBool()</method>
        <method>ConsumeFloat()</method>
        <method>ConsumeDouble()</method>
      </cpp>
    </from_end>

    <from_beginning>
      <java>
        <method>consumeString(maxLength)</method>
        <method>consumeRemainingAsString()</method>
        <method>consumeBytes(maxLength)</method>
        <method>consumeRemainingAsBytes()</method>
      </java>
      <cpp>
        <method>ConsumeRandomLengthString(maxLength)</method>
        <method>ConsumeString(maxLength)</method>
        <method>ConsumeRemainingBytes()</method>
      </cpp>
    </from_beginning>
  </methods>

  <example>
    <raw_bytes>[0x7B...0x7D, 0x03, 0x12, 0x34, 0x56, 0x78, 0x2A]</raw_bytes>
    <code language="java">
      public static void fuzzerTestOneInput(FuzzedDataProvider data) {
          // Control values (from end)
          int choice = data.consumeInt(0, 3);       // Consumes 0x2A → 2
          int value = data.consumeInt();            // Consumes [0x12, 0x34, 0x56, 0x78] → 0x78563412 (little endian)
          boolean flag = data.consumeBool();        // Consumes 0x03 → true

          // Data values (from beginning)
          String json = data.consumeRemainingAsString(); // Consumes [0x7B...0x7D] → {"name":"test"}

          // Use values to drive test
          if (choice == 0) {
              processString(json);
          } else if (choice == 1 && flag) {
              processStringWithFlag(json);
          }
      }
    </code>
  </example>
</FuzzedDataProvider>
""".strip()
