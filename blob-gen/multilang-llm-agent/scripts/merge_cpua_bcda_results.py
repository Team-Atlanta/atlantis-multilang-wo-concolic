#!/usr/bin/env python3
import glob
import json
import os
from datetime import datetime


def parse_date_from_filename(filename):
    """Extract date from filename like 2025-04-07_00-31-32.json"""
    base = os.path.basename(filename)
    date_str = base.split(".")[0]  # Remove extension
    return datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")


def load_jsonl(file_path):
    """Load JSONL file line by line with error handling"""
    with open(file_path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def merge_bcda_results(directory):
    """Merge BCDA results, updating BITs based on newer data"""
    # Exclude merged_results files
    files = [
        f
        for f in glob.glob(os.path.join(directory, "*.jsonl"))
        if not os.path.basename(f).startswith("merged_results")
    ]

    print(f"Found {len(files)} BCDA files to process")

    if not files:
        print(f"Warning: No .jsonl files found in {directory}")
        return {"BITs": []}

    try:
        # Sort files by date
        files.sort(key=parse_date_from_filename)
        print(f"Processing files in order: {[os.path.basename(f) for f in files]}")
    except ValueError as e:
        print(f"Warning: Error sorting files by date: {e}")
        return {"BITs": []}

    results = {}
    for file_path in files:
        print(f"\nProcessing file: {os.path.basename(file_path)}")
        try:
            # Try reading line by line first
            data = load_jsonl(file_path)
            print(f"Successfully loaded {len(data)} items from JSONL")
            for item in data.get("BITs", {}):
                if isinstance(item, dict):
                    key = (item["harness_name"], item["func_location"]["func_name"])
                    results[key] = item
                else:
                    print(f"Warning: Item is not a dictionary: {type(item)}")
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            continue

    return {"BITs": list(results.values())}


def merge_cpua_results(directory):
    """Merge CPUA results, updating CGs based on newer data"""
    result = {}
    # Exclude merged_results files
    files = [
        f
        for f in glob.glob(os.path.join(directory, "*.json"))
        if not os.path.basename(f).startswith("merged_results")
    ]
    files.extend(
        [
            f
            for f in glob.glob(os.path.join(directory, "*.jsonl"))
            if not os.path.basename(f).startswith("merged_results")
        ]
    )

    if not files:
        print(f"Warning: No .json/.jsonl files found in {directory}")
        return {"CGs": {}}

    try:
        # Sort files by date
        files.sort(key=parse_date_from_filename)
    except ValueError as e:
        print(f"Warning: Error sorting files by date: {e}")
        return {"CGs": {}}

    for file_path in files:
        with open(file_path, "r") as f:
            data = json.load(f)

        # Handle the CGs structure
        cgs = data.get("CGs", {})
        for project_name, cg_list in cgs.items():
            if project_name not in result:
                result[project_name] = {}

            for cg in cg_list:
                # Extract name and path
                name = cg.get("name")

                result[project_name][name] = cg

    # Convert back to original format
    final_result = {
        "CGs": {},
        "input_sources": [],
        "vuln_sink_functions": [],
    }
    for project_name, cg_dict in result.items():
        # Convert back to list, preserving only the CG data (not the keys)
        final_result["CGs"][project_name] = list(cg_dict.values())

    return final_result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Merge BCDA and CPUA results")
    parser.add_argument(
        "directory", help="Directory containing bcda and cpua subdirectories"
    )
    args = parser.parse_args()

    bcda_dir = os.path.join(args.directory, "bcda")
    cpua_dir = os.path.join(args.directory, "cpua")

    if not os.path.exists(bcda_dir) or not os.path.exists(cpua_dir):
        print(
            "Error: Both 'bcda' and 'cpua' subdirectories must exist in"
            f" {args.directory}"
        )
        return

    # Get current datetime in the same format
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Merge BCDA results
    bcda_results = merge_bcda_results(bcda_dir)
    merged_bcda_path = os.path.join(bcda_dir, f"{current_time}.jsonl")
    with open(merged_bcda_path, "w") as f:
        json.dump(bcda_results, f, indent=2)
    print(f"Merged BCDA results written to: {merged_bcda_path}")

    # Merge CPUA results
    cpua_results = merge_cpua_results(cpua_dir)
    merged_cpua_path = os.path.join(cpua_dir, f"{current_time}.json")
    with open(merged_cpua_path, "w") as f:
        json.dump(cpua_results, f, indent=2)
    print(f"Merged CPUA results written to: {merged_cpua_path}")


if __name__ == "__main__":
    main()
