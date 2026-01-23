#! /usr/bin/env python3

import argparse
import os
import re
import shutil
from datetime import datetime


def parse_timestamp(name):
    """
    Extracts a timestamp in the format 'YYYY-MM-DD_HH-MM-SS' from a file or
    directory name
    and returns it as a datetime object.
    """
    pattern = r".*(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})"
    match = re.match(pattern, name)
    if match:
        ts_str = match.group(1)
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None
    return None


def parse_cli_timestamp(date_str, is_after=False):
    """
    Parses a timestamp from a CLI argument that may be in full format or date-only.
    If a date-only (YYYY-MM-DD) is provided, it sets the time to:
      - 00:00:00 for a --before threshold,
      - 23:59:59 for an --after threshold.
    For the --on option, only the date part is considered.
    """
    try:
        # Try to parse full timestamp
        return datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        try:
            # Parse date-only and set default time
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if is_after:
                dt = dt.replace(hour=23, minute=59, second=59)
            else:
                dt = dt.replace(hour=0, minute=0, second=0)
            return dt
        except ValueError:
            return None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Script to delete files/directories in the 'results/' folder based on their"
            " timestamp."
        )
    )
    parser.add_argument(
        "--before",
        type=str,
        help=(
            "Delete files/directories created before the specified timestamp (format:"
            " YYYY-MM-DD or YYYY-MM-DD_HH-MM-SS)"
        ),
    )
    parser.add_argument(
        "--after",
        type=str,
        help=(
            "Delete files/directories created after the specified timestamp (format:"
            " YYYY-MM-DD or YYYY-MM-DD_HH-MM-SS)"
        ),
    )
    parser.add_argument(
        "--on",
        type=str,
        help=(
            "Delete files/directories created on the specified date (format: YYYY-MM-DD"
            " or YYYY-MM-DD_HH-MM-SS)"
        ),
    )
    parser.add_argument(
        "--results",
        type=str,
        default="results",
        help="Path to the results folder to search (default: results)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Perform a dry run that only prints which items would be deleted without"
            " actually deleting them"
        ),
    )
    args = parser.parse_args()

    before_ts = None
    after_ts = None
    on_ts = None
    on_date = None

    if args.before:
        before_ts = parse_cli_timestamp(args.before, is_after=False)
        if before_ts is None:
            print(
                "Error: The format of the --before option is incorrect. (e.g.,"
                " 2025-02-20 or 2025-02-20_21-55-35)"
            )
            return

    if args.after:
        after_ts = parse_cli_timestamp(args.after, is_after=True)
        if after_ts is None:
            print(
                "Error: The format of the --after option is incorrect. (e.g.,"
                " 2025-02-20 or 2025-02-20_21-55-35)"
            )
            return

    if args.on:
        on_ts = parse_cli_timestamp(args.on, is_after=False)
        if on_ts is None:
            print(
                "Error: The format of the --on option is incorrect. (e.g., 2025-02-20"
                " or 2025-02-20_21-55-35)"
            )
            return
        on_date = on_ts.date()

    if before_ts is None and after_ts is None and on_date is None:
        print(
            "Error: At least one of the --before, --after, or --on options must be"
            " specified."
        )
        return

    # Recursively traverse the results folder (processing subdirectories first)
    for root, dirs, files in os.walk(args.results, topdown=False):
        # Process files
        for name in files:
            timestamp = parse_timestamp(name)
            if timestamp:
                remove = False
                if before_ts and timestamp < before_ts:
                    remove = True
                if after_ts and timestamp > after_ts:
                    remove = True
                if on_date and timestamp.date() == on_date:
                    remove = True
                if remove:
                    path = os.path.join(root, name)
                    print("Removing file:", path)
                    if not args.dry_run:
                        os.remove(path)

        # Process directories
        for name in dirs:
            timestamp = parse_timestamp(name)
            if timestamp:
                remove = False
                if before_ts and timestamp < before_ts:
                    remove = True
                if after_ts and timestamp > after_ts:
                    remove = True
                if on_date and timestamp.date() == on_date:
                    remove = True
                if remove:
                    path = os.path.join(root, name)
                    print("Removing directory:", path)
                    if not args.dry_run:
                        shutil.rmtree(path)


if __name__ == "__main__":
    main()
