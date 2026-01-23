import random
from pathlib import Path
from typing import Optional, Tuple


def read_random_lines(path: Path, num_lines: int = 300) -> str:
    with path.open("r") as file:
        lines = file.readlines()

    total_lines = len(lines)

    if total_lines <= num_lines:
        return "\n".join(lines)

    start_line = random.randint(0, total_lines - num_lines)
    end_line = start_line + num_lines

    return "\n".join(lines[start_line:end_line])


def find_string_in_file(
    file_path: str, target: str, line_number: Optional[int] = None
) -> list[tuple[int, int]]:
    """
    Finds the occurrences of a given string in a file and returns their line and
    column positions.

    :param file_path: Path to the file to be searched
    :param target: The string to find
    :return: A list of tuples containing (line, column) positions. 1-based index.
    """
    positions = []

    with open(file_path, "r", encoding="utf-8") as file:
        for _line_number, line in enumerate(file):
            # line_number is 1-based index
            if line_number is None or _line_number == line_number - 1:
                column_number = line.find(target)
                if column_number != -1:
                    positions.append(
                        (_line_number + 1, column_number + 1)
                    )  # Convert to
                    # 1-based index

    return positions


# Utility functions
def instrument_line(func_body: str, start_number: int = 1) -> Tuple[str, int]:
    """Add line numbers to code for better analysis"""
    lines = func_body.splitlines()
    instrumented_lines = []
    for i, line in enumerate(lines, start_number):
        instrumented_lines.append(f"[{i}]: {line}")
    return "\n".join(instrumented_lines), i


def normalize_func_name(name: str) -> str:
    """Extract function name from fully qualified name"""
    name = name.split("(")[0] if "(" in name else name
    name = name.split(" ")[-1] if " " in name else name
    # if ".<init>" in name or ".<clinit>" in name:
    #     pass
    # else:
    name = name.split(".")[-1] if "." in name else name
    name = name.split("::")[-1] if "::" in name else name
    name = name.split("[")[0] if "[" in name else name
    name = name.split("]")[0] if "]" in name else name
    return name


def normalize_func_name_for_ci(name: str) -> str:
    """Extract function name from fully qualified name"""
    name = name.split("(")[0] if "(" in name else name
    name = name.split(" ")[-1] if " " in name else name
    if name.endswith(".<init>") or name.endswith(".<clinit>"):
        name = name.split(".")[-2]
    else:
        name = name.split(".")[-1] if "." in name else name
    name = name.split("::")[-1] if "::" in name else name
    name = name.split("[")[0] if "[" in name else name
    name = name.split("]")[0] if "]" in name else name
    return name


def get_function_body(file_path: str, start_line: int, end_line: int) -> str:
    with open(file_path, "r") as file:
        lines = file.readlines()
    return "".join(lines[start_line - 1 : end_line])


def get_callsite(file_path: str, line: int) -> str:
    with open(file_path, "r") as file:
        lines = file.readlines()
    return "".join(lines[line - 1 : line + 1])
