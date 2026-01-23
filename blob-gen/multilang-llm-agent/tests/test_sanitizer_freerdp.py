from mlla.modules.sanitizer import BaseSanitizer, GenericSanitizer


def test_detect_freerdp_mppc_error():
    """Test detection of FreeRDP MPPC codec error."""
    output = """[20:10:40:287] [3706632:00388f08] [WARN][com.freerdp.codec.rfx] - [winpr_log_backtrace_ex]: 10: dli_fname=/out/TestFuzzCodecs [0x555555554000], dli_sname=(null) [(nil)]
[20:10:40:287] [3706632:00388f08] [WARN][com.freerdp.codec.rfx] - [winpr_log_backtrace_ex]: 11: dli_fname=/lib/x86_64-linux-gnu/libc.so.6 [0x7ffff7b00000], dli_sname=__libc_start_main [0x7ffff7b23f90]
[20:10:40:287] [3706632:00388f08] [WARN][com.freerdp.codec.rfx] - [winpr_log_backtrace_ex]: 12: dli_fname=/out/TestFuzzCodecs [0x555555554000], dli_sname=(null) [(nil)]
[20:10:40:287] [3706632:00388f08] [WARN][com.freerdp.codec.rfx] - [winpr_log_backtrace_ex]: 13: unresolvable, address=(nil)]
[20:10:40:308] [3706632:00388f08] [ERROR][com.freerdp.codec.mppc] - [mppc_decompress]: history buffer index out of range
[20:10:40:308] [3706632:00388f08] [ERROR][com.freerdp.codec.mppc] - [mppc_decompress]: history buffer overflow
"""  # noqa: E501

    # Test GenericSanitizer detection
    triggered_generic, sanitizer_type_generic = GenericSanitizer.detect(output)
    assert not triggered_generic, "GenericSanitizer should not detect the error"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert not triggered_base, "BaseSanitizer should not detect the error"
