import os
import re
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from loguru import logger

from mlla.utils.code_tags import END_EXPLOIT_GUIDE_TAG, EXPLOIT_GUIDE_TAG

# Mapping from short names to sanitizer class names
SANITIZER_NAME_MAP = {
    "jazzer": "JazzerSanitizer",
    "address": "AddressSanitizer",
    "memory": "MemorySanitizer",
    "undefined": "UndefinedBehaviorSanitizer",
    "thread": "ThreadSanitizer",
    "leak": "LeakSanitizer",
    "generic": "GenericSanitizer",
}


def get_exploit_prompt(sanitizer_list) -> str:
    sanitizer_info: List[Dict[str, Any]] = []
    sanitizer_info_str = ""
    for sanitizer_name in sanitizer_list:
        sanitizer_info = get_sanitizer_info(sanitizer_name)

        if sanitizer_info:
            for info in sanitizer_info:
                sanitizer_info_str += "<sanitizer>\n"
                sanitizer_info_str += "  <type>\n"
                sanitizer_info_str += f"  {info['sanitizer_type']}\n"
                sanitizer_info_str += "  </type>\n"
                if "description" in info and info["description"]:
                    sanitizer_info_str += "  <description>\n"
                    sanitizer_info_str += f"  {info['description']}\n"
                    sanitizer_info_str += "  </description>\n"
                if "exploit" in info and info["exploit"]:
                    sanitizer_info_str += "  <exploit>\n"
                    sanitizer_info_str += f"  {info['exploit']}\n"
                    sanitizer_info_str += "  </exploit>\n"
                sanitizer_info_str += "</sanitizer>\n"

    output_str = ""
    if sanitizer_info_str:
        output_str += f"{EXPLOIT_GUIDE_TAG}\n"
        output_str += f"{sanitizer_info_str}".strip() + "\n"
        output_str += f"{END_EXPLOIT_GUIDE_TAG}"

    return output_str


def get_sanitizer_prompt(sanitizer_list, with_exploit: bool = False) -> str:

    sanitizer_info: List[Dict[str, Any]] = []
    sanitizer_info_str = ""
    for sanitizer_name in sanitizer_list:
        sanitizer_info = get_sanitizer_info(sanitizer_name)

        # Add sanitizer description to system message if available
        if sanitizer_info:
            for info in sanitizer_info:
                sanitizer_info_str += "<sanitizer>\n"
                sanitizer_info_str += "<type>\n"
                sanitizer_info_str += f"{info['sanitizer_type']}\n"
                sanitizer_info_str += "</type>\n"
                if "description" in info and info["description"]:
                    sanitizer_info_str += "<description>\n"
                    sanitizer_info_str += f"{info['description']}\n"
                    sanitizer_info_str += "</description>\n"
                if with_exploit and "exploit" in info and info["exploit"]:
                    sanitizer_info_str += "<exploit>\n"
                    sanitizer_info_str += f"{info['exploit']}\n"
                    sanitizer_info_str += "</exploit>\n"
                sanitizer_info_str += "</sanitizer>\n"

    if sanitizer_info and len(sanitizer_info) > 1:
        sanitizer_candidates_str = "<sanitizer_candidates>\n"
        sanitizer_candidates_str += sanitizer_info_str
        sanitizer_candidates_str += "</sanitizer_candidates>\n"
    else:
        sanitizer_candidates_str = sanitizer_info_str

    return sanitizer_candidates_str


def is_known_crash(sanitizer_info: str) -> bool:
    """Check if a detected crash is a known vulnerability type."""
    if not sanitizer_info:
        return False

    # Filter out generic Java exceptions (can have class name prefix)
    if "JavaException." in sanitizer_info:
        return False

    # Filter out unknown crash types (can have class name prefix)
    if "unknown." in sanitizer_info:
        return False

    # Skip timeout bugs if not allowed
    allow_timeout_bug = os.getenv("ALLOW_TIMEOUT_BUG", False)
    if not allow_timeout_bug:
        if "Timeout" in sanitizer_info or "InfiniteLoop" in sanitizer_info:
            return False

    # All other crashes are considered known security vulnerabilities
    return True


def get_sanitizer_classname(sanitizer_name: str) -> str:
    sanitizer_class = (
        sanitizer_name.split(".")[0] if "." in sanitizer_name else sanitizer_name
    )

    if not sanitizer_class.endswith("Sanitizer"):
        sanitizer_class = SANITIZER_NAME_MAP.get(
            sanitizer_class.lower(), sanitizer_class
        )

    return sanitizer_class


def get_sanitizer_info(sanitizer_name: str) -> List[Dict[str, Any]]:
    """Load sanitizer information from YAML description files only."""
    # Parse sanitizer type to get class and subtype
    parts = sanitizer_name.split(".")
    sanitizer_class = parts[0]

    base_type = None
    if len(parts) > 1:
        base_type = parts[1]

    # Get sanitizer class from base type
    if not sanitizer_class.endswith("Sanitizer"):
        sanitizer_class = SANITIZER_NAME_MAP.get(
            sanitizer_class.lower(), sanitizer_class
        )

    # Load from YAML file only
    try:
        desc_file = (
            Path(__file__).parent
            / "sanitizer_info"
            / f"{sanitizer_class}_with_exploit.yaml"
        )
        with open(desc_file) as f:
            descriptions = yaml.safe_load(f)

        if not descriptions:
            return []

        result = []
        for type_name, info in descriptions.items():
            # Skip if base type doesn't match
            if base_type and type_name != base_type:
                continue

            # Skip timeout bugs if not allowed
            allow_timeout_bug = os.getenv("ALLOW_TIMEOUT_BUG", False)
            if not allow_timeout_bug:
                if "Timeout" in type_name or "InfiniteLoop" in type_name:
                    continue

            # All entries now have flat structure with description and exploit
            if "description" in info:
                result.append(
                    {
                        "sanitizer_type": type_name,
                        "sentinel": info.get("sentinel", ""),
                        "description": info["description"],
                        "exploit": info.get("exploit", ""),
                    }
                )

        return result

    except (FileNotFoundError, yaml.YAMLError) as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.warning(f"Invalid sanitizer descriptions: {error_msg}")

        # Return empty list if YAML file not found or invalid
        return []


def get_sanitizer_list(sanitizer_name: str) -> list[str]:
    """Get the list of top-level sanitizer types from YAML files."""
    sanitizer_info = get_sanitizer_info(sanitizer_name)

    # Extract top-level types (remove subtypes for LLM reasoning)
    top_level_types = set()
    for info in sanitizer_info:
        sanitizer_type = info["sanitizer_type"]
        if "." in sanitizer_type:
            # Extract parent type (e.g., "LdapInjection" <- "LdapInjection.name_chars")
            parent_type = sanitizer_type.split(".")[0]
            top_level_types.add(parent_type)
        else:
            # Simple type without subtypes
            top_level_types.add(sanitizer_type)

    return sorted(list(top_level_types))


class BaseSanitizer(ABC):
    """Base class for all sanitizers with common functionality."""

    # Default empty set for sanitizer types
    _sanitizers: Dict = {}

    def __init__(self):
        pass

    @classmethod
    @abstractmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """
        Abstract method that must be implemented by all sanitizer classes.
        Returns (is_triggered, sanitizer_type).
        """
        pass

    @classmethod
    def detect_crash_type(cls, output: str) -> Tuple[bool, str]:
        """
        Generic crash detection for different sanitizers.
        Returns (is_crash, sanitizer_type).
        """
        # Check each registered sanitizer type
        for sanitizer_cls in [
            JazzerSanitizer,
            AddressSanitizer,
            LeakSanitizer,
            MemorySanitizer,
            ThreadSanitizer,
            UndefinedBehaviorSanitizer,
            GenericSanitizer,
        ]:
            triggered, sanitizer_type = sanitizer_cls.detect(output)
            if triggered:
                return True, f"{sanitizer_cls.__name__}.{sanitizer_type}"
        return False, ""


class JazzerSanitizer(BaseSanitizer):
    """Jazzer-specific sanitizer detection."""

    # Mapping from exact strings to sanitizer type names
    _sanitizers: Dict = {
        "File path traversal": "FilePathTraversal",
        "LDAP Injection": "LdapInjection",
        "OS Command Injection": "OsCommandInjection",
        "Remote JNDI Lookup": "RemoteJndiLookup",  # NamingContextLookup
        "Script Engine Injection": "ScriptEngineInjection",
        "load arbitrary library": "ReflectiveCall",
        "SQL Injection": "SQLInjection",
        "XPath Injection": "XPathInjection",
        "Remote Code Execution": (
            "RemoteCodeExecution"
        ),  # Deserialization, ExpressionLanguageInjection, ReflectiveCall
        "Regular Expression Injection": "RegexInjection",
        "Server Side Request Forgery": "ServerSideRequestForgery",
        # "Integer Overflow": "IntegerOverflow", # deprecated after ASC
        "Out of memory": "OutOfMemory",
        "Stack overflow": "StackOverflow",
        "timeout": "TimeoutDenialOfService",  # will be handled by libfuzzer
    }

    # Jazzer error pattern - capture Java Exception messages
    _pattern = r"== Java Exception: ([^\n]+)"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect Jazzer sanitizer crashes."""
        # Try to find Java Exception in the output
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            full_exception = sanitizer_match.group(1).strip()

            # Check if any known sanitizer type appears in the exception message
            for known_type in cls._sanitizers:
                if known_type in full_exception:
                    # Use mapping if available, otherwise return as-is
                    return True, cls._sanitizers[known_type]

            # For any other Java Exception, use the full exception as-is
            exception_class = full_exception.split(":")[0].strip()
            return True, f"JavaException.{exception_class}"

        return False, ""


class AddressSanitizer(BaseSanitizer):
    """AddressSanitizer (ASan) detection."""

    # Mapping from error strings to sanitizer type names
    _sanitizers = {
        # Signal-based error types
        "FPE": "FPE",
        "negative-size-param": "negative-size-param",
        "double-free": "double-free",
        "SEGV": "SEGV",
        "ABRT": "ABRT",
        "ILL": "ILL",
        # Special cases
        "attempting free on address which was not malloc": "free-without-malloc",
        # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/asan/asan_errors.cpp#L435
        # Default fallback
        "unknown-crash": "unknown-crash",
        # Core error types
        "heap-buffer-overflow": "heap-buffer-overflow",
        "heap-use-after-free": "heap-use-after-free",
        "stack-buffer-underflow": "stack-buffer-underflow",
        "initialization-order-fiasco": "initialization-order-fiasco",
        "stack-buffer-overflow": "stack-buffer-overflow",
        "stack-use-after-return": "stack-use-after-return",
        "use-after-poison": "use-after-poison",
        "container-overflow": "container-overflow",
        "stack-use-after-scope": "stack-use-after-scope",
        "global-buffer-overflow": "global-buffer-overflow",
        "intra-object-overflow": "intra-object-overflow",
        "dynamic-stack-buffer-underflow": "dynamic-stack-buffer-underflow",
        # Additional error types from other sources
        # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/asan/asan_errors.h#L397
        "invalid-pointer-pair": "invalid-pointer-pair",  # From pointer-compare tests
    }

    # ASan error pattern - capture the full error message including details
    _pattern = r"==\d+==ERROR: AddressSanitizer: ([^(]+)(?:\(.*\))?"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect AddressSanitizer crashes."""
        # Try to find the specific sanitizer type in the ASan error message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            sanitizer_type = sanitizer_match.group(1).strip()

            # Check for known sanitizer types
            for known_type in cls._sanitizers:
                if known_type in sanitizer_type:
                    return True, cls._sanitizers[known_type]

            # If it's unknown, return it with unknown prefix
            return True, f"unknown.{sanitizer_type}"

        return False, ""


class LeakSanitizer(BaseSanitizer):
    """LeakSanitizer (LSan) detection."""

    # LeakSanitizer is called in AddressSanitizer"""
    # https://github.com/llvm/llvm-project/tree/main/compiler-rt/lib/asan#readme

    # Mapping from error strings to sanitizer type names
    _sanitizers = {
        "detected memory leaks": "detected memory leaks",
    }

    # LSan error pattern matching the exact format
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/lsan/lsan_common.cpp#L851
    _pattern = r"==\d+==ERROR: LeakSanitizer: ([a-zA-Z- \(\)\.]+)"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect LeakSanitizer memory leaks."""
        # Try to find the LSan error message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            sanitizer_type = sanitizer_match.group(1)

            # Check if any known sanitizer type appears in the error
            for known_type in cls._sanitizers:
                if known_type in sanitizer_type:
                    return True, cls._sanitizers[known_type]

            # If no known type is found, return as unknown
            return True, f"unknown.{sanitizer_type}"

        return False, ""


class MemorySanitizer(BaseSanitizer):
    """MemorySanitizer (MSan) detection."""

    # Mapping from error strings to sanitizer type names
    _sanitizers = {
        "use-of-uninitialized-value": "use-of-uninitialized-value",
        "UMR": "UMR",
    }

    # MSan error pattern matching the exact format
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/msan/msan_report.cpp#L109
    _pattern = r"==\d+==WARNING: MemorySanitizer: ([a-zA-Z-]+)"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect MemorySanitizer warnings."""
        # Try to find the specific sanitizer type in the LSan warning message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            sanitizer_type = sanitizer_match.group(1)

            # Check if any known sanitizer type appears in the error
            for known_type in cls._sanitizers:
                if known_type in sanitizer_type:
                    return True, cls._sanitizers[known_type]

            # If no known type is found, return as unknown
            return True, f"unknown.{sanitizer_type}"

        return False, ""


class ThreadSanitizer(BaseSanitizer):
    """ThreadSanitizer (TSan) detection."""

    # Mapping from error strings to sanitizer type names
    # Known TSan error types from tsan_report.cpp
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/tsan/rtl/tsan_report.cpp#L62
    _sanitizers = {
        "SEGV": "SEGV",
        "data race": "data race",
        "data race on vptr": "data race on vptr",
        "heap-use-after-free": "heap-use-after-free",
        "heap-use-after-free (virtual call vs free)": (
            "heap-use-after-free (virtual call vs free)"
        ),
        "race on external object": "race on external object",
        "thread leak": "thread leak",
        "destroy of a locked mutex": "destroy of a locked mutex",
        "double lock of a mutex": "double lock of a mutex",
        "use of an invalid mutex": "use of an invalid mutex",
        "unlock of an unlocked mutex": "unlock of an unlocked mutex",
        "read lock of a write locked mutex": "read lock of a write locked mutex",
        "read unlock of a write locked mutex": "read unlock of a write locked mutex",
        "signal-unsafe call inside of a signal": (
            "signal-unsafe call inside of a signal"
        ),
        "signal handler spoils errno": "signal handler spoils errno",
        "lock-order-inversion": "lock-order-inversion",
        "mutex held in the wrong context": "mutex held in the wrong context",
    }

    # TSan error pattern matching the exact format
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/tsan/rtl/tsan_report.cpp#L287
    _pattern = r"==\d+==ERROR: ThreadSanitizer: ([a-zA-Z- \(\)\.\/]+)"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect ThreadSanitizer warnings."""
        # Try to find the TSan warning message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            sanitizer_type = sanitizer_match.group(1)

            # Check if any known sanitizer type appears in the error
            for known_type in cls._sanitizers:
                if known_type in sanitizer_type:
                    return True, cls._sanitizers[known_type]

            # If no known type is found, return as unknown
            return True, f"unknown.{sanitizer_type}"

        return False, ""


class UndefinedBehaviorSanitizer(BaseSanitizer):
    """UndefinedBehaviorSanitizer (UBSan) detection."""

    # Mapping from error strings to sanitizer type names
    # Known UBSan error types from ubsan_checks.inc
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/ubsan/ubsan_checks.inc
    # However, these are not actually used in the output.
    # Thus, the below sanitizers are just a reference.
    _sanitizers = {
        "SEGV": "SEGV",
        "undefined-behavior": "undefined-behavior",
        "null-pointer-use": "null-pointer-use",
        "nullptr-with-offset": "nullptr-with-offset",
        "nullptr-with-nonzero-offset": "nullptr-with-nonzero-offset",
        "nullptr-after-nonzero-offset": "nullptr-after-nonzero-offset",
        "pointer-overflow": "pointer-overflow",
        "misaligned-pointer-use": "misaligned-pointer-use",
        "alignment-assumption": "alignment-assumption",
        "insufficient-object-size": "insufficient-object-size",
        "signed-integer-overflow": "signed-integer-overflow",
        "unsigned-integer-overflow": "unsigned-integer-overflow",
        "integer-divide-by-zero": "integer-divide-by-zero",
        "float-divide-by-zero": "float-divide-by-zero",
        "invalid-builtin-use": "invalid-builtin-use",
        "invalid-objc-cast": "invalid-objc-cast",
        "implicit-unsigned-integer-truncation": "implicit-unsigned-integer-truncation",
        "implicit-signed-integer-truncation": "implicit-signed-integer-truncation",
        "implicit-integer-sign-change": "implicit-integer-sign-change",
        "implicit-signed-integer-truncation-or-sign-change": (
            "implicit-signed-integer-truncation-or-sign-change"
        ),
        "invalid-shift-base": "invalid-shift-base",
        "invalid-shift-exponent": "invalid-shift-exponent",
        "out-of-bounds-index": "out-of-bounds-index",
        "local-out-of-bounds": "local-out-of-bounds",
        "unreachable-call": "unreachable-call",
        "missing-return": "missing-return",
        "non-positive-vla-index": "non-positive-vla-index",
        "float-cast-overflow": "float-cast-overflow",
        "invalid-bool-load": "invalid-bool-load",
        "invalid-enum-load": "invalid-enum-load",
        "function-type-mismatch": "function-type-mismatch",
        "invalid-null-return": "invalid-null-return",
        "invalid-null-argument": "invalid-null-argument",
        "dynamic-type-mismatch": "dynamic-type-mismatch",
        "cfi-bad-type": "cfi-bad-type",
    }

    # UBSan error pattern matching runtime errors
    # https://github.com/llvm/llvm-project/blob/main/compiler-rt/lib/ubsan/ubsan_handlers.cpp
    _pattern = r"==\d+==ERROR: UndefinedBehaviorSanitizer: ([a-zA-Z- \(\)\.\/]+)"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect UndefinedBehaviorSanitizer crashes."""
        # Try to find the UBSan error message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            sanitizer_type = sanitizer_match.group(1).strip()

            # Check for known sanitizer types
            for known_type in cls._sanitizers:
                if known_type in sanitizer_type:
                    return True, cls._sanitizers[known_type]

            # If it's unknown, return it with unknown prefix
            return True, f"unknown.{sanitizer_type}"

        return False, ""


class GenericSanitizer(BaseSanitizer):
    """Generic crash indicators and fuzzer-specific patterns."""

    # Mapping from error strings to sanitizer type names
    _sanitizers = {
        "out-of-memory": "out-of-memory",
        "timeout": "timeout",
        "fuzz target exited": "fuzz-target-exited",
    }

    # LibFuzzer error pattern - capture the full error message
    _pattern = r"==\d+== ERROR: libFuzzer: ([^(]+)(?:\(.*\))?"

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect generic crashes and fuzzer-specific patterns."""
        # Try to find the libFuzzer error message
        sanitizer_match = re.search(cls._pattern, output)
        if sanitizer_match:
            error_type = sanitizer_match.group(1).strip()

            # Check for known error types
            for known_type in cls._sanitizers:
                if known_type in error_type:
                    return True, cls._sanitizers[known_type]

            # If it's unknown, return it with unknown prefix
            return True, f"unknown.{error_type}"

        return False, ""


class LLVMSanitizer(BaseSanitizer):
    """LLVM sanitizer detection (ASan, MSan, UBSan, TSan, etc.)"""

    # Core sanitizer patterns from LLVM
    _sanitizer_patterns = [
        # AddressSanitizer (ASan)
        (r"==\d+==ERROR: AddressSanitizer:", "address"),
        # # MemorySanitizer (MSan)
        (r"==\d+==WARNING: MemorySanitizer:", "memory"),
        # ThreadSanitizer (TSan)
        (r"==\d+==ERROR: ThreadSanitizer:", "thread"),
        # LeakSanitizer (LSan)
        (r"==\d+==ERROR: LeakSanitizer:", "leak"),
        # UndefinedBehaviorSanitizer (UBSan)
        (r"==\d+==ERROR: UndefinedBehaviorSanitizer:", "undefined"),
        # (r"runtime error:", "undefined"),
        # # (r"^[A-Za-z0-9_]+\.c(pp)?:\d+:\d+: runtime error:", "undefined")
    ]

    @classmethod
    def detect(cls, output: str) -> Tuple[bool, str]:
        """Detect LLVM sanitizer crashes."""
        # Check each sanitizer pattern in order of specificity
        for pattern, sanitizer_type in cls._sanitizer_patterns:
            if re.search(pattern, output, re.MULTILINE):
                return True, sanitizer_type

        return False, ""


# For backward compatibility
Sanitizer = BaseSanitizer
