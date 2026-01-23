from mlla.modules.known_struct import (
    JVM_BYTE_BUFFER_TAG,
    extract_method_calls,
    get_known_struct_prompts,
)
from mlla.modules.known_struct_info.fdp import (
    detect_language,
    generate_method_mapping,
    get_base_method_names,
)


class TestMethodExtraction:
    def test_extract_method_calls(self):
        # Test extracting method calls from Java code
        java_code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            int choice = data.consumeInt(0, 3);
            String str = data.consumeRemainingAsString();
        }
        """
        methods = extract_method_calls(java_code, "FuzzedDataProvider")
        assert methods == {"consumeInt", "consumeRemainingAsString"}

        # Test extracting method calls from C++ code
        cpp_code = """
        extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
            FuzzedDataProvider fdp(data, size);
            int choice = fdp.ConsumeIntegralInRange<int>(0, 3);
            std::string str = fdp.ConsumeRemainingBytesAsString();
            return 0;
        }
        """
        methods = extract_method_calls(cpp_code, "FuzzedDataProvider")
        assert methods == {"ConsumeIntegralInRange", "ConsumeRemainingBytesAsString"}

        # Test with no method calls
        empty_code = "public class Test {}"
        methods = extract_method_calls(empty_code, "FuzzedDataProvider")
        assert methods == set()


class TestLanguageDetection:
    def test_detect_java(self):
        java_code = """
        import java.util.Random;

        public class Test {
            public static void fuzzerTestOneInput(FuzzedDataProvider data) {
                int value = data.consumeInt();
            }
        }
        """
        assert detect_language(java_code) == "jvm"

    def test_detect_cpp(self):
        cpp_code = """
        #include <cstdint>
        #include <string>

        extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
            FuzzedDataProvider fdp(data, size);
            int value = fdp.ConsumeIntegral<int>();
            return 0;
        }
        """
        assert detect_language(cpp_code) == "llvm"

    def test_ambiguous_defaults_to_jvm(self):
        ambiguous_code = """
        // This code has no clear language indicators
        void test() {
            // Some code here
        }
        """
        assert detect_language(ambiguous_code) == "jvm"


class TestPromptGeneration:
    def test_java_prompt_with_filtering(self):
        # Use specific method names that we know exist in JAZZER_METHODS
        java_code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            int choice = data.consumeInt(0, 3);
            String str = data.consumeRemainingAsString();
        }
        """

        prompt = get_known_struct_prompts(java_code)

        # Check that the prompt contains the FDP tag
        assert "<FuzzedDataProvider>" in prompt

        # Check that the prompt contains the used methods
        assert "consumeInt" in prompt
        assert "consumeRemainingAsString" in prompt

        # Check that unused methods are filtered out
        # We know these methods exist in JAZZER_METHODS but aren't used in our test code
        unused_methods = ["consumeByte", "consumeBoolean", "consumeFloat"]

        # Check a few unused methods
        mapping_section = prompt.split("<method_mapping>")[1].split(
            "</method_mapping>"
        )[0]
        for method in unused_methods:
            # The method should not appear with parentheses (indicating a method call)
            # in the mapping section
            assert f"{method}(" not in mapping_section

    def test_cpp_prompt_with_filtering(self):
        # Use specific method names that we know exist in LLVM_METHODS
        cpp_code = """
        extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
            FuzzedDataProvider fdp(data, size);
            int choice = fdp.ConsumeIntegralInRange<int>(0, 3);
            std::string str = fdp.ConsumeRemainingBytesAsString();
            return 0;
        }
        """

        prompt = get_known_struct_prompts(cpp_code)

        # Check that the prompt contains the FDP tag
        assert "<FuzzedDataProvider>" in prompt

        # Check that the prompt contains the used methods
        assert "ConsumeIntegralInRange" in prompt
        assert "ConsumeRemainingBytesAsString" in prompt

        # Check that unused methods are filtered out
        # We know these methods exist in LLVM_METHODS but aren't used in our test code
        unused_methods = ["ConsumeBytes", "ConsumeBool", "ConsumeFloatingPoint"]

        # Check a few unused methods
        mapping_section = prompt.split("<method_mapping>")[1].split(
            "</method_mapping>"
        )[0]
        for method in unused_methods:
            # The method should not appear with parentheses (indicating a method call)
            # in the mapping section
            assert f"{method}(" not in mapping_section

    def test_no_filtering_when_no_methods_found(self):
        # Code with FDP but no method calls
        code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            // No method calls here
            System.out.println("Hello, world!");
        }
        """
        prompt = get_known_struct_prompts(code)

        assert prompt == ""

    def test_byte_buffer_prompt(self):
        # Code with ByteBuffer
        code = """
        public static void test() {
            ByteBuffer buffer = ByteBuffer.allocate(1024);
            buffer.putInt(42);
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains the ByteBuffer tag
        assert JVM_BYTE_BUFFER_TAG in prompt

    def test_no_prompt_for_unknown_struct(self):
        # Code with no known structures
        code = """
        public static void test() {
            // No known structures here
            System.out.println("Hello, world!");
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt is empty
        assert prompt == ""


class TestUtilityFunctions:
    def test_generate_method_mapping(self):
        # Create a simple method dictionary
        methods_dict = {
            "testMethod": [
                ("testMethod(int param)", "test_producer(target: int, param: int)"),
                ("testMethod()", "test_producer(target: int)"),
            ]
        }

        mapping = generate_method_mapping(methods_dict)

        # Check that the mapping contains both methods
        assert (
            "testMethod(int param) → test_producer(target: int, param: int)" in mapping
        )
        assert "testMethod() → test_producer(target: int)" in mapping

    def test_get_base_method_names(self):
        # Create a simple method dictionary
        methods_dict = {
            "method1": [("method1()", "producer1()")],
            "method2": [("method2(int param)", "producer2(int)")],
        }

        base_names = get_base_method_names(methods_dict)

        # Check that the base names are extracted correctly
        assert base_names == {"method1", "method2"}
