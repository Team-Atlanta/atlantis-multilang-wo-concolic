# flake8: noqa: E501
JVM_BYTE_BUFFER_PROMPT = """
<ByteBuffer>
  <description>
    ByteBuffer is a utility class in Java that specially handles integer value in BIG-ENDIAN.
  </description>

  <core_principles>
    <principle>Default is BIG-ENDIAN byte order (most significant byte first)</principle>
  </core_principles>

  <methods>
    <primitive_getters>
      <method>getInt()</method>
      <method>getLong()</method>
    </primitive_getters>
  </methods>

  <example>
    <raw_bytes>[0x01, 0x02, 0x03, 0x04, 0x41, 0x42, 0x43, 0x44]</raw_bytes>
    <code language="java">
      public static void fuzzerTestOneInput(byte[] data) {
          if (data.length < 4) return; // Ensure we have enough data

          ByteBuffer buf = ByteBuffer.wrap(data);

          // BIG-ENDIAN reading (default)
          int value = buf.getInt();  // Reads [0x01, 0x02, 0x03, 0x04] → 0x01020304

          // Use value to drive test
          if (value > 0) {
              processData(value);
          }
      }
    </code>
  </example>
</ByteBuffer>
""".strip()
