import pytest

from mlla.utils.jvm_type_converter import decode_java_type, decode_method_signature


def test_decode_basic_types():
    """Test basic Java type conversions."""
    assert decode_java_type("I") == "int"
    assert decode_java_type("V") == "void"
    assert decode_java_type("Z") == "boolean"
    assert decode_java_type("J") == "long"
    assert decode_java_type("D") == "double"


def test_decode_array_types():
    """Test array type conversions."""
    assert decode_java_type("[I") == "int[]"
    assert decode_java_type("[[I") == "int[][]"
    assert decode_java_type("[Ljava/lang/String;") == "String[]"


def test_decode_object_types():
    """Test object type conversions."""
    assert decode_java_type("Ljava/lang/String;") == "String"
    assert decode_java_type("Ljava/util/List;") == "List"
    assert decode_java_type("Lcom/example/CustomClass;") == "CustomClass"


def test_decode_empty_type():
    """Test handling of empty input."""
    assert decode_java_type("") == "void"


def test_decode_method_signature_basic():
    """Test basic method signature parsing."""
    signature = "test(II)V"
    return_and_name, params = decode_method_signature(signature)
    assert return_and_name == "void test"
    assert params == ["int", "int"]


def test_decode_method_signature_complex():
    """Test complex method signature parsing."""
    signature = "processData(Ljava/lang/String;[I[[Ljava/lang/Object;)Ljava/util/List;"
    return_and_name, params = decode_method_signature(signature)
    assert return_and_name == "List processData"
    assert params == ["String", "int[]", "Object[][]"]


def test_decode_method_signature_empty():
    """Test handling of empty method signature."""
    with pytest.raises(ValueError, match="Invalid signature: Empty input"):
        decode_method_signature("")


def test_decode_method_signature_invalid():
    """Test handling of invalid method signature."""
    with pytest.raises(
        ValueError,
        match="Invalid signature: Unmatched parentheses in invalid\\(signature",
    ):
        decode_method_signature("invalid(signature")


def test_decode_method_no_params():
    """Test method signature with no parameters."""
    signature = "isEmpty()Z"
    return_and_name, params = decode_method_signature(signature)
    assert return_and_name == "boolean isEmpty"
    assert params == []


def test_jenkins_three_signatures():
    """Test JenkinsThree class method signatures."""
    test_cases = [
        (
            "com.aixcc.jenkins.harnesses.three.JenkinsThree.<init>()V",
            "void com.aixcc.jenkins.harnesses.three.JenkinsThree.<init>",
            [],
        ),
        (
            "com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzzerTestOneInput([B)V",
            "void com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzzerTestOneInput",
            ["byte[]"],
        ),
        (
            "com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzz([B)V",
            "void com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzz",
            ["byte[]"],
        ),
        (
            (
                "com.aixcc.jenkins.harnesses.three.JenkinsThree.testAuthAction"
                "(Ljava/nio/ByteBuffer;)V"
            ),
            "void com.aixcc.jenkins.harnesses.three.JenkinsThree.testAuthAction",
            ["ByteBuffer"],
        ),
        (
            (
                "com.aixcc.jenkins.harnesses.three.JenkinsThree.getRemainingAsString"
                "(Ljava/nio/ByteBuffer;)Ljava/lang/String;"
            ),
            (
                "String"
                " com.aixcc.jenkins.harnesses.three.JenkinsThree.getRemainingAsString"
            ),
            ["ByteBuffer"],
        ),
    ]

    for signature, expected_return_and_name, expected_params in test_cases:
        return_and_name, params = decode_method_signature(signature)
        assert return_and_name == expected_return_and_name, f"Failed for {signature}"
        assert params == expected_params, f"Failed for {signature}"
