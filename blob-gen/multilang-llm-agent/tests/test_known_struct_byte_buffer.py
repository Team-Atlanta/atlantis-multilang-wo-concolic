from mlla.modules.known_struct import JVM_BYTE_BUFFER_TAG, get_known_struct_prompts


class TestByteBuffer:
    def test_byte_buffer_detection(self):
        # Code with ByteBuffer
        code = """
        public static void test() {
            ByteBuffer buffer = ByteBuffer.allocate(1024);
            buffer.putInt(42);
            int value = buffer.getInt();
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains the ByteBuffer tag
        assert JVM_BYTE_BUFFER_TAG in prompt
        assert "<ByteBuffer>" in prompt
        assert "BIG-ENDIAN" in prompt

    def test_byte_buffer_with_wrap(self):
        # Code with ByteBuffer.wrap
        code = """
        public static void fuzzerTestOneInput(byte[] data) {
            if (data.length < 4) return;
            ByteBuffer buf = ByteBuffer.wrap(data);
            int value = buf.getInt();
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains ByteBuffer information
        assert JVM_BYTE_BUFFER_TAG in prompt
        assert "getInt()" in prompt
        assert "BIG-ENDIAN" in prompt

    def test_no_byte_buffer_detection(self):
        # Code without ByteBuffer
        code = """
        public class RegularClass {
            public void method() {
                String text = "hello world";
                System.out.println(text);
            }
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that ByteBuffer is not detected
        assert JVM_BYTE_BUFFER_TAG not in prompt
        assert "<ByteBuffer>" not in prompt

    def test_byte_buffer_prompt_content(self):
        # Code with ByteBuffer
        code = """
        ByteBuffer buffer = ByteBuffer.allocate(8);
        long value = buffer.getLong();
        """
        prompt = get_known_struct_prompts(code)

        # Check specific content in the prompt
        assert "BIG-ENDIAN" in prompt
        assert "most significant byte first" in prompt
        assert "getInt()" in prompt
        assert "getLong()" in prompt
