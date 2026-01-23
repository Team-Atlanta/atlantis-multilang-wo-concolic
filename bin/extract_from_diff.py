#!/usr/bin/env python3

import re
import sys
import json
from collections import defaultdict


def to_utf8(data):
    try:
        return data.decode("utf-8")
    except:
        return None


def parse_diff(file_path):
    new_files = []
    updated_files = []
    changes_by_file = defaultdict(list)

    current_file = None

    with open(file_path, "rb") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        # new flie
        if line.startswith(b"--- /dev/null"):
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            match = re.match(rb"^\+\+\+ (.+)", next_line)
            if match:
                filename = to_utf8(match.group(1))
                new_files.append(filename)
                current_file = filename

        # update file
        elif line.startswith(b"+++ ") and not line.startswith(b"+++ /dev/null"):
            match = re.match(rb"^\+\+\+ (.+)", line)
            if match:
                filename = to_utf8(match.group(1))
                updated_files.append(filename)
                current_file = filename

        # update/added lines
        elif line.startswith(b"@@") and current_file:
            match = re.search(rb"\+(\d+)(?:,(\d+))?", line)
            if match:
                start_line = int(match.group(1))
                line_count = int(match.group(2)) if match.group(2) else 1
                if line_count > 0:
                    changes_by_file[current_file].append(
                        (start_line, start_line + line_count - 1)
                    )
    ret = {}
    for file, changes in changes_by_file.items():
        new_file = "/src/repo"+file[1:]
        ext = new_file.split(".")[-1]
        if ext in ["java", "kt", "c", "h", "cpp", "cc", "hpp"]:
            ret[new_file] = changes
    return ret 


def main(diff_file, output_file):
    changes = parse_diff(diff_file)
    with open(output_file, "wt") as f:
        f.write(json.dumps(changes))


if __name__ == "__main__":
    diff_file = sys.argv[1]
    output_file = sys.argv[2]
    main(diff_file, output_file)