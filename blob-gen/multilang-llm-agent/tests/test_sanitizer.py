# flake8: noqa: E501
import pytest

from mlla.modules.sanitizer import (
    get_sanitizer_classname,
    get_sanitizer_info,
    get_sanitizer_list,
    is_known_crash,
)


def test_get_sanitizer_info_specific_type():
    """Test get_sanitizer_info with specific sanitizer type."""
    info = get_sanitizer_info("jazzer.SQLInjection")
    assert info is not None
    assert len(info) == 1
    assert info[0]["sanitizer_type"] == "SQLInjection"
    assert "SQL" in info[0]["description"]


def test_get_sanitizer_info_all_types():
    """Test get_sanitizer_info to get all sanitizer types."""
    info = get_sanitizer_info("jazzer")
    assert info is not None
    assert len(info) > 10
    types = {i["sanitizer_type"] for i in info}
    assert "SQLInjection" in types
    assert "LDAPInjection" in types


def test_get_sanitizer_info_invalid_class():
    """Test get_sanitizer_info with invalid sanitizer class."""
    info = get_sanitizer_info("nonexistent")
    assert info == []


def test_get_sanitizer_info_invalid_type():
    """Test get_sanitizer_info with invalid sanitizer type."""
    info = get_sanitizer_info("jazzer.NonExistentType")
    assert info == []


def test_get_sanitizer_info_address_sanitizer():
    """Test get_sanitizer_info with AddressSanitizer."""
    info = get_sanitizer_info("address.BufferOverflow")
    assert info is not None
    assert len(info) == 1
    assert info[0]["sanitizer_type"] == "BufferOverflow"
    assert info[0]["description"] != ""


def test_get_sanitizer_list():
    """Test get_sanitizer_list function."""
    # Test valid sanitizer
    types = get_sanitizer_list("jazzer")
    assert isinstance(types, list)
    assert "SQLInjection" in types
    assert "LDAPInjection" in types

    # Test invalid sanitizer
    types = get_sanitizer_list("invalid")
    assert types == []


def test_get_sanitizer_classname():
    """Test get_sanitizer_classname function."""
    # Test with already complete sanitizer names
    assert get_sanitizer_classname("JazzerSanitizer") == "JazzerSanitizer"
    assert get_sanitizer_classname("AddressSanitizer") == "AddressSanitizer"

    # Test with mapped names
    assert get_sanitizer_classname("jazzer") == "JazzerSanitizer"
    assert get_sanitizer_classname("address") == "AddressSanitizer"
    assert get_sanitizer_classname("leak") == "LeakSanitizer"

    # Test with sanitizer name in dot notation (both short and complete names)
    assert get_sanitizer_classname("jazzer.SQLInjection") == "JazzerSanitizer"
    assert get_sanitizer_classname("address.heap-buffer-overflow") == "AddressSanitizer"
    assert get_sanitizer_classname("JazzerSanitizer.SQLInjection") == "JazzerSanitizer"
    assert (
        get_sanitizer_classname("AddressSanitizer.heap-buffer-overflow")
        == "AddressSanitizer"
    )


def test_is_known_crash():
    """Test is_known_crash function."""
    # Test empty input
    assert is_known_crash("") is False
    assert is_known_crash(None) is False

    # Test known vulnerabilities
    assert is_known_crash("JazzerSanitizer.SQLInjection") is True
    assert is_known_crash("AddressSanitizer.heap-buffer-overflow") is True
    assert is_known_crash("SQLInjection") is True

    # Test filtered crashes
    assert (
        is_known_crash(
            "JazzerSanitizer.JavaException.java.lang.ArrayIndexOutOfBoundsException"
        )
        is False
    )
    assert is_known_crash("AddressSanitizer.unknown.some-crash") is False
    assert is_known_crash("JavaException.java.lang.NullPointerException") is False
    assert is_known_crash("unknown.random-crash-type") is False

    # Test edge cases
    assert is_known_crash("SomeVulnerability.JavaException.inside") is False
    assert is_known_crash("JazzerSanitizer.UnknownVulnerability") is True


if __name__ == "__main__":
    pytest.main()
