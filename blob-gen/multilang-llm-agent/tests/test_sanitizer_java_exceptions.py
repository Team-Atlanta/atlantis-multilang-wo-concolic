# flake8: noqa: E501
import pytest

from mlla.modules.sanitizer import JazzerSanitizer


def test_detect_file_not_found_exception_normalization():
    """Test that FileNotFoundException with different paths are normalized to the same type."""
    # Test case 1: FileNotFoundException with first temp directory
    output1 = """== Java Exception: java.io.FileNotFoundException: /tmp/testRecoverCoverage9188578375146493683/coverage-report (No such file or directory)"""
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.io.FileNotFoundException"

    # Test case 2: FileNotFoundException with different temp directory
    output2 = """== Java Exception: java.io.FileNotFoundException: /tmp/testRecoverCoverage16313223031751007437/coverage-report (No such file or directory)"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.io.FileNotFoundException"

    # Test case 3: FileNotFoundException with another different temp directory
    output3 = """== Java Exception: java.io.FileNotFoundException: /tmp/testRecoverCoverage14199987692974707520/coverage-report (No such file or directory)"""
    triggered3, sanitizer_type3 = JazzerSanitizer.detect(output3)
    assert triggered3
    assert sanitizer_type3 == "JavaException.java.io.FileNotFoundException"

    # All should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2 == sanitizer_type3


def test_detect_file_not_found_exception_different_files():
    """Test that FileNotFoundException with different file paths are normalized."""
    # Test case 1: Different file path
    output1 = """== Java Exception: java.io.FileNotFoundException: /var/log/application.log (Permission denied)"""
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.io.FileNotFoundException"

    # Test case 2: Another different file path
    output2 = """== Java Exception: java.io.FileNotFoundException: /home/user/config.properties (No such file or directory)"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.io.FileNotFoundException"

    # Both should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2


def test_detect_io_exception_normalization():
    """Test that IOException with different messages are normalized."""
    # Test case 1: IOException with specific message
    output1 = """== Java Exception: java.io.IOException: Connection reset by peer"""
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.io.IOException"

    # Test case 2: IOException with different message
    output2 = """== Java Exception: java.io.IOException: Broken pipe (Write failed)"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.io.IOException"

    # Both should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2


def test_detect_null_pointer_exception_normalization():
    """Test that NullPointerException with different messages are normalized."""
    # Test case 1: NPE with specific location
    output1 = """== Java Exception: java.lang.NullPointerException: Cannot invoke "String.length()" because "str" is null"""
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.lang.NullPointerException"

    # Test case 2: NPE with different location
    output2 = """== Java Exception: java.lang.NullPointerException: Cannot read field "value" because "obj" is null"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.lang.NullPointerException"

    # Both should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2


def test_detect_class_not_found_exception_normalization():
    """Test that ClassNotFoundException with different class names are normalized."""
    # Test case 1: ClassNotFoundException with specific class
    output1 = (
        """== Java Exception: java.lang.ClassNotFoundException: com.example.MyClass"""
    )
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.lang.ClassNotFoundException"

    # Test case 2: ClassNotFoundException with different class
    output2 = """== Java Exception: java.lang.ClassNotFoundException: org.apache.commons.SomeUtility"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.lang.ClassNotFoundException"

    # Both should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2


def test_detect_number_format_exception_normalization():
    """Test that NumberFormatException with different input values are normalized."""
    # Test case 1: NumberFormatException with specific input
    output1 = """== Java Exception: java.lang.NumberFormatException: For input string: "abc123"""
    triggered1, sanitizer_type1 = JazzerSanitizer.detect(output1)
    assert triggered1
    assert sanitizer_type1 == "JavaException.java.lang.NumberFormatException"

    # Test case 2: NumberFormatException with different input
    output2 = """== Java Exception: java.lang.NumberFormatException: For input string: "xyz789"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert sanitizer_type2 == "JavaException.java.lang.NumberFormatException"

    # Both should be normalized to the same sanitizer type
    assert sanitizer_type1 == sanitizer_type2


def test_detect_exception_without_colon():
    """Test that exceptions without colon in message are handled correctly."""
    # Test case: Exception without additional message
    output = """== Java Exception: java.lang.RuntimeException"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "JavaException.java.lang.RuntimeException"


def test_detect_exception_with_multiple_colons_but_timeout():
    """Test that exceptions with multiple colons are handled correctly."""
    # Test case: Exception with multiple colons in message
    output = """== Java Exception: java.sql.SQLException: Connection failed: timeout: 30000ms"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "TimeoutDenialOfService"


def test_detect_known_sanitizer_types_still_work():
    """Test that known sanitizer types are still detected correctly and not normalized."""
    # Test case: Known SQL Injection should still return the mapped type
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh: SQL Injection"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert (
        sanitizer_type == "SQLInjection"
    )  # Should use the mapped type, not be normalized

    # Test case: Known File path traversal should still return the mapped type
    output2 = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: File path traversal: /tmp/test"""
    triggered2, sanitizer_type2 = JazzerSanitizer.detect(output2)
    assert triggered2
    assert (
        sanitizer_type2 == "FilePathTraversal"
    )  # Should use the mapped type, not be normalized


if __name__ == "__main__":
    pytest.main()
