# flake8: noqa: E501
import pytest

from mlla.modules.sanitizer import (
    AddressSanitizer,
    BaseSanitizer,
    GenericSanitizer,
    ThreadSanitizer,
    UndefinedBehaviorSanitizer,
)


def test_thread_sanitizer_segv():
    """Test detection of ThreadSanitizer SEGV on unknown address."""
    output = """==14==ERROR: ThreadSanitizer: SEGV on unknown address 0x000000000000 (pc 0x000000000000 bp 0x7ffe697b16ef sp 0x7ffe697b16d8 T14)"""

    # Test ThreadSanitizer detection
    triggered, sanitizer_type = ThreadSanitizer.detect(output)
    assert triggered
    assert "SEGV" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "ThreadSanitizer.SEGV"


def test_libfuzzer_timeout():
    """Test detection of libFuzzer timeout."""
    output = """==16== ERROR: libFuzzer: timeout after 25 seconds"""

    # Test GenericSanitizer detection
    triggered, sanitizer_type = GenericSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "timeout"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "GenericSanitizer.timeout"


def test_address_sanitizer_heap_buffer_overflow():
    """Test detection of AddressSanitizer heap-buffer-overflow."""
    output = """==16==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x62900004f20c at pc 0x7fbd9d3f8742 bp 0x7ffed0234800 sp 0x7ffed02347f0"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "heap-buffer-overflow"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.heap-buffer-overflow"


def test_address_sanitizer_invalid_free():
    """Test detection of AddressSanitizer attempting free on non-malloc address."""
    output = """==16==ERROR: AddressSanitizer: attempting free on address which was not malloc()-ed: 0x603000363eb0 in thread T0"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "free-without-malloc"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.free-without-malloc"


def test_address_sanitizer_use_after_free():
    """Test detection of AddressSanitizer heap-use-after-free."""
    output = """==14==ERROR: AddressSanitizer: heap-use-after-free on address 0x603000000100 at pc 0x55c3220ff917 bp 0x7ffdbd8a5430 sp 0x7ffdbd8a5428"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "heap-use-after-free"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.heap-use-after-free"


def test_address_sanitizer_abrt():
    """Test detection of AddressSanitizer ABRT on unknown address."""
    output = """==14==ERROR: AddressSanitizer: ABRT on unknown address 0x00000000000e (pc 0x7f0b3fb6c00b bp 0x7ffdc40d4540 sp 0x7ffdc40d42f0 T0)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "ABRT"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.ABRT"


def test_undefined_behavior_sanitizer_segv():
    """Test detection of UndefinedBehaviorSanitizer SEGV on unknown address."""
    output = """==14==ERROR: UndefinedBehaviorSanitizer: SEGV on unknown address 0x55cf0b1fd089 (pc 0x55ceb0284f1b bp 0x7ffef29be180 sp 0x7ffef29be0e0 T14)"""

    # Test UndefinedBehaviorSanitizer detection
    triggered, sanitizer_type = UndefinedBehaviorSanitizer.detect(output)
    assert triggered
    assert "SEGV" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "UndefinedBehaviorSanitizer.SEGV"


def test_address_sanitizer_segv_with_address():
    """Test detection of AddressSanitizer SEGV on specific address."""
    output = """==14==ERROR: AddressSanitizer: SEGV on unknown address 0x7fa18823a4e4 (pc 0x55bde6ab31f1 bp 0x7f9f7cbff8f0 sp 0x7f9f7cbff7e0 T20)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert "SEGV" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.SEGV"


def test_address_sanitizer_segv_null_pointer():
    """Test detection of AddressSanitizer SEGV on null pointer."""
    output = """==14==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000 (pc 0x557d6e269482 bp 0x7ffe5af6f5b0 sp 0x7ffe5af6f4a0 T0)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert "SEGV" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.SEGV"


def test_address_sanitizer_fpe():
    """Test detection of AddressSanitizer FPE on unknown address."""
    output = """==2061==ERROR: AddressSanitizer: FPE on unknown address 0x562e9827aff0 (pc 0x562e9827aff0 bp 0x7ffd08a1b970 sp 0x7ffd08a1b8e0 T0)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "FPE"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.FPE"


def test_address_sanitizer_stack_buffer_overflow():
    """Test detection of AddressSanitizer stack-buffer-overflow."""
    output = """==463189==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7f81f2700060 at pc 0x5595f60a2034 bp 0x7ffc9e68b930 sp 0x7ffc9e68b0f0"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "stack-buffer-overflow"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.stack-buffer-overflow"


def test_address_sanitizer_heap_buffer_overflow_2():
    """Test detection of AddressSanitizer heap-buffer-overflow (second case)."""
    output = """==549404==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x5020000000a0 at pc 0x55747ae5e034 bp 0x7ffca3d0e040 sp 0x7ffca3d0d800"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "heap-buffer-overflow"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.heap-buffer-overflow"


def test_undefined_behavior_sanitizer_segv_small_address():
    """Test detection of UndefinedBehaviorSanitizer SEGV on small non-zero address."""
    output = """==14==ERROR: UndefinedBehaviorSanitizer: SEGV on unknown address 0x000000000004 (pc 0x5598bff3aa3a bp 0x7ffdcdafa2b0 sp 0x7ffdcdafa1c0 T14)"""

    # Test UndefinedBehaviorSanitizer detection
    triggered, sanitizer_type = UndefinedBehaviorSanitizer.detect(output)
    assert triggered
    assert "SEGV" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "UndefinedBehaviorSanitizer.SEGV"


def test_address_sanitizer_dynamic_stack_buffer_overflow():
    """Test detection of AddressSanitizer dynamic-stack-buffer-overflow."""
    output = """==18==ERROR: AddressSanitizer: dynamic-stack-buffer-overflow on address 0x7fff8d4e98b2 at pc 0x5620ec34aa9b bp 0x7fff8d4e9830 sp 0x7fff8d4e9828"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    # The current implementation matches "stack-buffer-overflow" as a substring
    assert sanitizer_type == "stack-buffer-overflow"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.stack-buffer-overflow"


def test_address_sanitizer_ill():
    """Test detection of AddressSanitizer ILL on unknown address."""
    output = """==14==ERROR: AddressSanitizer: ILL on unknown address 0x557fa6210dec (pc 0x557fa6210dec bp 0x7fffd235af00 sp 0x7fffd235ad20 T0)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert "ILL" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.ILL"


def test_address_sanitizer_double_free():
    """Test detection of AddressSanitizer attempting double-free."""
    output = """==1635==ERROR: AddressSanitizer: attempting double-free on 0x502000000130 in thread T0:"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert "double-free" in sanitizer_type

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.double-free"


def test_address_sanitizer_unknown_crash():
    """Test detection of AddressSanitizer unknown-crash."""
    output = """==14==ERROR: AddressSanitizer: unknown-crash on address 0x123456789abcdef at pc 0x5574df64ab7c bp 0x7fff29f4e540 sp 0x7fff29f4dce8"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "unknown-crash"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.unknown-crash"


def test_address_sanitizer_global_buffer_overflow():
    """Test detection of AddressSanitizer global-buffer-overflow."""
    output = """==14==ERROR: AddressSanitizer: global-buffer-overflow on address 0x558e5eaf695f at pc 0x558e5e086ef4 bp 0x7ffff1f8ace0 sp 0x7ffff1f8acd8"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "global-buffer-overflow"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.global-buffer-overflow"


def test_address_sanitizer_negative_size_param():
    """Test detection of AddressSanitizer negative-size-param."""
    output = """==14==ERROR: AddressSanitizer: negative-size-param: (size=-1)"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert "negative-size-param" in sanitizer_type.lower()

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.negative-size-param"


def test_address_sanitizer_stack_use_after_return():
    """Test detection of AddressSanitizer stack-use-after-return."""
    output = """==18==ERROR: AddressSanitizer: stack-use-after-return on address 0x7f1aedf59f60 at pc 0x5564acc566ce bp 0x7ffecde623b0 sp 0x7ffecde623a8"""

    # Test AddressSanitizer detection
    triggered, sanitizer_type = AddressSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "stack-use-after-return"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "AddressSanitizer.stack-use-after-return"


def test_libfuzzer_out_of_memory():
    """Test detection of libFuzzer out-of-memory."""
    output = """==14== ERROR: libFuzzer: out-of-memory (malloc(3489662144))"""

    # Test GenericSanitizer detection
    triggered, sanitizer_type = GenericSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "out-of-memory"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "GenericSanitizer.out-of-memory"


def test_libfuzzer_fuzz_target_exited():
    """Test detection of libFuzzer fuzz target exited."""
    output = """==18== ERROR: libFuzzer: fuzz target exited"""

    # Test GenericSanitizer detection
    triggered, sanitizer_type = GenericSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "fuzz-target-exited"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "GenericSanitizer.fuzz-target-exited"


if __name__ == "__main__":
    pytest.main()
