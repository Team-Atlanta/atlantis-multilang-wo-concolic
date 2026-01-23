from mlla.modules.known_struct import (
    FUZZED_DATA_PROVIDER_TAG,
    extract_method_calls,
    get_known_struct_prompts,
)
from mlla.modules.known_struct_info.fdp import detect_language


class TestFuzzedDataProvider:
    def test_java_fuzzed_data_provider_detection(self):
        # Java code with FuzzedDataProvider
        code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            int choice = data.consumeInt(0, 3);
            String str = data.consumeRemainingAsString();
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains the FuzzedDataProvider tag
        assert "<FuzzedDataProvider>" in prompt
        assert "consumeInt" in prompt
        assert "consumeRemainingAsString" in prompt
        assert "JazzerFdpEncoder" in prompt

    def test_cpp_fuzzed_data_provider_detection(self):
        # C++ code with FuzzedDataProvider
        code = """
        extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
            FuzzedDataProvider fdp(data, size);
            int choice = fdp.ConsumeIntegralInRange<int>(0, 3);
            std::string str = fdp.ConsumeRemainingBytesAsString();
            return 0;
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains the FuzzedDataProvider tag
        assert "<FuzzedDataProvider>" in prompt
        assert "ConsumeIntegralInRange" in prompt
        assert "ConsumeRemainingBytesAsString" in prompt
        assert "LlvmFdpEncoder" in prompt

    def test_no_fuzzed_data_provider_detection(self):
        # Code without FuzzedDataProvider
        code = """
        public class RegularClass {
            public void method() {
                int value = 42;
                System.out.println(value);
            }
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that FuzzedDataProvider is not detected
        assert FUZZED_DATA_PROVIDER_TAG not in prompt
        assert "<FuzzedDataProvider>" not in prompt

    def test_method_extraction_java(self):
        # Test extracting method calls from Java code
        java_code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            int choice = data.consumeInt(0, 3);
            String str = data.consumeRemainingAsString();
            boolean flag = data.consumeBoolean();
        }
        """
        methods = extract_method_calls(java_code, "FuzzedDataProvider")
        assert methods == {"consumeInt", "consumeRemainingAsString", "consumeBoolean"}

    def test_method_extraction_cpp(self):
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

    def test_language_detection_java(self):
        java_code = """
        import java.util.Random;

        public class Test {
            public static void fuzzerTestOneInput(FuzzedDataProvider data) {
                int value = data.consumeInt();
            }
        }
        """
        assert detect_language(java_code) == "jvm"

    def test_language_detection_cpp(self):
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

    def test_fuzzed_data_provider_with_no_methods(self):
        # Code with FDP but no method calls
        code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            // No method calls here
            System.out.println("Hello, world!");
        }
        """
        prompt = get_known_struct_prompts(code)

        # Should return empty prompt when no methods are found
        assert prompt == ""

    def test_fuzzed_data_provider_method_filtering(self):
        # Use specific method names that exist in JAZZER_METHODS
        java_code = """
        public static void fuzzerTestOneInput(FuzzedDataProvider data) {
            int choice = data.consumeInt(0, 3);
            String str = data.consumeRemainingAsString();
        }
        """

        prompt = get_known_struct_prompts(java_code)

        # Check that the prompt contains the used methods
        assert "consumeInt" in prompt
        assert "consumeRemainingAsString" in prompt

        # Check that unused methods are filtered out
        unused_methods = ["consumeByte", "consumeBoolean", "consumeFloat"]
        mapping_section = prompt.split("<method_mapping>")[1].split(
            "</method_mapping>"
        )[0]

        for method in unused_methods:
            # The method should not appear with parentheses in the mapping section
            assert f"{method}(" not in mapping_section
