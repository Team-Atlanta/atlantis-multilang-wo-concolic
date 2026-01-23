import re
from typing import List, Tuple

# JVM type mapping
JAVA_TYPE_MAP = {
    "V": "void",
    "Z": "boolean",
    "B": "byte",
    "C": "char",
    "S": "short",
    "I": "int",
    "J": "long",
    "F": "float",
    "D": "double",
}


def decode_java_type(signature: str) -> str:
    """Converts JVM Type Signature to a readable format."""
    if not signature:
        return "void"  # Default return type

    if signature.startswith("L") and signature.endswith(";"):
        # Extract only the class name from fully qualified name
        return signature[1:-1].replace("/", ".").split(".")[-1]
    elif signature.startswith("["):  # Handle array types
        return decode_java_type(signature[1:]) + "[]"
    return JAVA_TYPE_MAP.get(signature, signature)


def is_jvm_signature(signature: str) -> bool:
    """Check if the signature is a JVM signature."""
    # First check if it has the basic method signature pattern with parentheses
    if not re.match(r"[\w/.$<>]+\((.*)\)([A-ZV\[][\w/$;]*)", signature):
        return False

    # Check for JVM-specific indicators
    has_jvm_indicators = (
        any(c in signature for c in "ZBCSIFJDV")
        or ("L" in signature and ";" in signature)
        or "/" in signature
        or ".<init>" in signature
    )

    # Check for common C/C++ patterns that would indicate it's not a JVM signature
    has_cpp_indicators = (
        "::" in signature  # C++ class method
        or "->" in signature  # C++ lambda or pointer to member
    )

    return has_jvm_indicators and not has_cpp_indicators


def has_unmatched_parentheses(signature: str) -> bool:
    """Check if a signature has unmatched parentheses."""
    count = 0
    for char in signature:
        if char == "(":
            count += 1
        elif char == ")":
            count -= 1
        if count < 0:  # Closing parenthesis without matching opening
            return True
    return count != 0  # If count is not 0, there are unmatched parentheses


def decode_method_signature(signature: str) -> Tuple[str, List[str]]:
    """Parses a method signature and converts it."""
    if not signature:
        raise ValueError("Invalid signature: Empty input")

    # Check for unmatched parentheses
    if "(" in signature and has_unmatched_parentheses(signature):
        raise ValueError(f"Invalid signature: Unmatched parentheses in {signature}")

    # Check if this is a JVM signature
    if not is_jvm_signature(signature):
        # For C functions, we might just have the function name without a signature
        # Just return it as is with empty params
        return signature, []

    # Process JVM signature
    match = re.match(r"([\w/.$<>]+)\((.*)\)([A-ZV\[][\w/$;]*)", signature)
    if not match:
        # This shouldn't happen since we already checked in is_jvm_signature
        return signature, []

    method_name = match.group(1).replace("/", ".")
    params = match.group(2)
    return_type = match.group(3)

    # Convert return type
    return_type_str = decode_java_type(return_type)

    if method_name.endswith(".<init>"):
        # TODO: handle constructor correctly
        # method_name = method_name[:-7]  # Remove .<init> <- this is incorrect
        pass

    # Convert parameter types
    param_types = []
    param_pattern = re.compile(r"(\[*L[\w/$]+;|\[*[BCDFIJSZ])")

    for param in param_pattern.findall(params):
        param_types.append(decode_java_type(param))

    return f"{return_type_str} {method_name}", param_types
