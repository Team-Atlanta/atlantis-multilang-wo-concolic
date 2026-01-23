"""Coverage comparison utilities."""

import asyncio
import json
import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from fuzzdb import CovInfo, FuzzDB
from loguru import logger

from ..utils import instrument_line, normalize_func_name
from ..utils.bit import BugInducingThing
from ..utils.jvm_type_converter import decode_method_signature


class InterestingSeedPolicy(Enum):
    CREATED_TIME = "created_time"
    LINE_COUNT = "line_count"
    FUNCTION_COUNT = "function_count"
    CG_COUNT = "cg_count"
    BIT_DISTANCE = "bit_distance"
    UNIAFL_SCORE = "uniafl_score"


class InterestingSeedPolicyContext:
    def __init__(self, policy: InterestingSeedPolicy):
        self.policy = policy


async def init_fuzzdb(harness_name) -> FuzzDB:
    config_path = os.getenv("UNIAFL_CONFIG")
    if not config_path:
        config_path = (
            f"/crs-workdir/worker-0/HarnessRunner/{harness_name}/uniafl/config"
        )

    while not os.path.exists(config_path):
        await asyncio.sleep(5)

    fuzzdb = FuzzDB(config_path)

    while not fuzzdb.corpus_dir.exists() or len(fuzzdb.list_seeds_new()) == 0:
        await asyncio.sleep(5)

    return fuzzdb


def cnt_covered_lines(fdb: FuzzDB, seed_fname: str) -> int:
    cov = fdb.load_node_cov(seed_fname)
    return sum(len(cov_info.lines) for cov_info in cov.values())


def cnt_covered_functions(fdb: FuzzDB, seed_fname: str) -> int:
    cov = fdb.load_node_cov(seed_fname)
    return len(cov)


def load_interesting_seed(
    fdb: FuzzDB,
    policy_ctx: InterestingSeedPolicyContext,
) -> Optional[tuple[Path, Dict[str, CovInfo]]]:
    seeds_list = fdb.list_seeds_new()

    if not seeds_list:
        logger.warning(f"No seeds found in fuzzdb: {fdb.corpus_dir}")
        return None

    seed_path: Optional[Path] = None

    policy = policy_ctx.policy

    if policy == InterestingSeedPolicy.CREATED_TIME:
        sorted_list = sorted(
            seeds_list,
            key=lambda x: os.path.getctime(x.directory / x.name),
            reverse=True,
        )
        seed = sorted_list[0]
        seed_path = seed.directory / seed.name
    elif policy == InterestingSeedPolicy.LINE_COUNT:
        sorted_list = sorted(
            seeds_list, key=lambda x: cnt_covered_lines(fdb, x.name), reverse=True
        )
        seed = sorted_list[0]
        seed_path = seed.directory / seed.name

    elif policy == InterestingSeedPolicy.FUNCTION_COUNT:
        sorted_list = sorted(
            seeds_list, key=lambda x: cnt_covered_functions(fdb, x.name), reverse=True
        )
        seed = sorted_list[0]
        seed_path = seed.directory / seed.name
    elif policy == InterestingSeedPolicy.CG_COUNT:
        pass
    elif policy == InterestingSeedPolicy.BIT_DISTANCE:
        pass
    elif policy == InterestingSeedPolicy.UNIAFL_SCORE:
        pass
    else:
        pass

    if seed_path:
        cov = fdb.load_node_cov(seed_path.name)
        return seed_path, cov

    return None


def cov_str_to_dict(coverage_str: str):
    cov = json.loads(coverage_str)
    cov_dict: Dict[str, Dict] = {}
    for func_sig, info in cov.items():
        func_name, _ = decode_method_signature(func_sig)

        if func_name not in cov_dict:
            cov_dict[func_name] = {}

        # Convert CovInfo object to dictionary
        cov_dict[func_name] = {"src": info["src"], "lines": info["lines"]}

    return cov_dict


def cov_class_to_dict(cov: Dict) -> Dict:
    """Convert coverage information"""
    cov_dict: Dict[str, CovInfo] = {}
    for func_sig, info in cov.items():
        func_name, _ = decode_method_signature(func_sig)

        if func_name not in cov_dict:
            cov_dict[func_name] = {}

        # Convert CovInfo object to dictionary
        cov_dict[func_name] = {"src": info.src, "lines": info.lines}

    return cov_dict


def load_all_coverage_info(fuzzdb: FuzzDB, harness_name: str) -> List[Dict]:
    """Load coverage information from all fuzzers for a given harness."""
    try:
        fdb = fuzzdb
        if not fdb:
            logger.debug(f"FuzzDB is not initialized for {harness_name}")
            return []

        coverage_per_seed = []

        # Iterate through all seeds and collect their coverage information
        seed_names = sorted([seed.name for seed in fdb.list_seeds_new()])
        for seed_fname in seed_names:
            # Load coverage information for this seed
            cov = fdb.load_node_cov(seed_fname)

            # Convert CovInfo objects to dictionaries
            cov_dict = cov_class_to_dict(cov)

            # Add this seed's coverage to the list
            coverage_per_seed.append(cov_dict)

        logger.info(f"Loaded coverage information for {len(coverage_per_seed)} seeds")
        return coverage_per_seed

    except Exception as e:
        logger.error(f"Error loading coverage information: {e}")
        return []


def is_transition_covered(
    src_func_name: str,
    src_file_path: str,
    dst_func_name: str,
    dst_file_path: str,
    coverage_info_list: List[Dict],
    language: str = "jvm",
) -> bool:
    """Check if a transition between two functions is already covered."""
    # Normalize input function names
    normalized_src = normalize_func_name(src_func_name)
    normalized_dst = normalize_func_name(dst_func_name)

    # Check each seed's coverage
    for seed_coverage in coverage_info_list:
        covered_funcs = set()

        # Process all function signatures and identify covered functions
        for func_name, info in seed_coverage.items():
            # Skip functions without coverage
            if not info.get("lines"):
                continue

            file_path = info.get("src")
            if not file_path:
                continue

            normalized_func = normalize_func_name(func_name)
            covered_funcs.add((normalized_func, file_path))

        # Check if both source and destination functions are covered in this seed
        # fmt: off
        if (
            (normalized_src, src_file_path) in covered_funcs
            and (normalized_dst, dst_file_path) in covered_funcs
        ):
            # fmd: on
            return True

    # If we get here, no seed covers both functions
    return False


def print_coverage_diff(coverage_diff: Dict) -> Optional[str]:
    """Format coverage differences and return as string if differences exist."""
    if not coverage_diff:
        return None

    lines = ["Coverage differences detected:"]
    lines.append("<COVERAGE_DIFF>")

    new_funcs = set()
    if coverage_diff["new_lines"]:
        lines.append("<new_coverage>")
        lines.append("<functions_with_line_counts>")
        for func, line_nums in sorted(coverage_diff["new_lines"].items()):
            # lines.append(f"- {func}: [{', '.join(map(str, line_nums))}]")
            lines.append(f"- {func}: {len(line_nums)} more lines")
            new_funcs.add(func)
        lines.append("</functions_with_line_counts>")

        # some functions might not be included
        if coverage_diff["new_functions"]:
            tmp_lines = []
            for func in sorted(coverage_diff["new_functions"]):
                if func not in new_funcs:
                    tmp_lines.append(f"- {func}")
            if tmp_lines:
                lines.append("\n<functions_only>")
                lines.extend(tmp_lines)
                lines.append("</functions_only>")
        lines.append("</new_coverage>")

    removed_funcs = set()
    if coverage_diff["removed_lines"]:
        lines.append("\n<reduced_coverage>")
        lines.append("<functions_with_line_counts>")
        for func, line_nums in sorted(coverage_diff["removed_lines"].items()):
            # lines.append(f"- {func}: [{', '.join(map(str, line_nums))}]")
            lines.append(f"- {func}: {len(line_nums)} less lines")
            removed_funcs.add(func)
        lines.append("</functions_with_line_counts>")

        # some functions might not be included
        if coverage_diff["removed_functions"]:
            tmp_lines = []
            for func in sorted(coverage_diff["removed_functions"]):
                if func not in removed_funcs:
                    tmp_lines.append(f"- {func}")
            if tmp_lines:
                lines.append("\n<functions_only>")
                lines.extend(tmp_lines)
                lines.append("</functions_only>")
        lines.append("</reduced_coverage>")

    lines.append("</COVERAGE_DIFF>")
    result = "\n".join(lines)
    logger.debug(result)
    return result


def is_path_already_crashed(cg, bit, previous_crashes, found_bits=None):
    """Check if a path has already led to a crash."""
    if found_bits is None:
        found_bits = []

    SKIP_ALREADY_CRASHED = os.getenv("ORCHESTRATOR_SKIP_ALREADY_CRASHED", False)
    if not SKIP_ALREADY_CRASHED:
        return False

    # Check previous crashes from checker output
    if previous_crashes:
        for crash in previous_crashes:
            # Check if CG matches
            if "cg_name" in crash and crash["cg_name"] == cg.name:
                # If there's no BIT info in the crash or no BIT in current path,
                # just match on CG name
                if ("bit_info" not in crash) or (bit is None):
                    logger.debug(f"Skipping {cg.name} as it already led to a crash")
                    return True

                # If both have BIT info, check if they match
                if bit and "bit_info" in crash:
                    bit_info = crash["bit_info"]
                    if compare_bits(bit, bit_info):
                        logger.debug(
                            f"Skipping BIT {bit.func_location.func_name} for {cg.name} "
                            "as it already led to a crash"
                        )
                        return True

    # Check previously found BITs from found_BITs directory
    if bit and found_bits:
        for found_bit in found_bits:
            # We assume that the function has a single vulnerability.
            if compare_bits(bit, found_bit):
                logger.debug(
                    f"Skipping BIT {bit.func_location.func_name} as it was previously"
                    " found"
                )
                return True

    return False


def load_found_bits(workdir):
    """Load information about previously found BITs from JSON files."""
    found_bits = []

    # Path to the found_BITs directory
    found_bits_dir = workdir / "found_BITs"
    logger.info(f"Checking previously found BITs in {found_bits_dir}")
    if not found_bits_dir.exists():
        return found_bits

    # Load all JSON files in the found_BITs directory
    for json_file in found_bits_dir.glob("*.json"):
        try:
            with json_file.open("r") as f:
                bit_data = json.load(f)
                found_bits.append(bit_data)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading BIT data from {json_file}: {e}")

    if found_bits:
        logger.info(f"Loaded {len(found_bits)} previously found BITs to filter out")

    return found_bits


def compare_bits(bit: BugInducingThing, found_bit_dict: Dict):
    """Compare BIT object with a single bit dictionary."""
    if not bit or not found_bit_dict:
        return False

    # Get BIT object location info
    bit_func_name = bit.func_location.func_name
    bit_file_path = bit.func_location.file_path

    # Handle different structures in found_bit_dict
    if "func_location" in found_bit_dict:
        # Structured format
        found_loc = found_bit_dict["func_location"]
        found_func_name = found_loc.get("func_name", "")
        found_file_path = found_loc.get("file_path", "")
    else:
        # Simple format
        found_func_name = found_bit_dict.get("func_name", "")
        found_file_path = found_bit_dict.get("file_path", "")

    # Check function name and file path match
    return bit_func_name == found_func_name and bit_file_path == found_file_path


def load_previous_crashes(out_dir, harness_name, sanitizer):
    """Load information about previously found crashes."""
    crashes = []

    # Path to the checker output directory
    # out_dir = ret_file.parent.joinpath("checker_output")
    if not out_dir.exists():
        return crashes

    # Load all JSON files in the checker output directory
    for json_file in out_dir.glob("*.json"):
        try:
            with json_file.open("r") as f:
                checker_data = json.load(f)

            # Check if this is for the same sanitizer
            if checker_data.get("sanitizer") != sanitizer:
                continue

            # Check if there are any crashed paths
            if "crash_info" in checker_data:
                for crash in checker_data["crash_info"]:
                    crashes.append(crash)

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading checker output from {json_file}: {e}")

    if crashes:
        logger.info(f"Loaded {len(crashes)} previously crashed paths to filter out")

    return crashes


def filter_coverage_by_func_list(coverage_info, func_list, language="jvm"):
    """Filter coverage information to only include functions in the list."""
    if not coverage_info or not func_list:
        return coverage_info

    # Create a set of normalized function names for efficient lookup
    normalized_func_set = {normalize_func_name(func) for func in func_list}

    filtered_coverage = {}

    for func_name, info in coverage_info.items():
        normalized_name = normalize_func_name(func_name)

        # Only include functions in the normalized set
        if normalized_name in normalized_func_set:
            filtered_coverage[normalized_name] = info

    return filtered_coverage


def compare_coverage(original_coverage: Dict, mutant_coverage: Dict) -> Dict:
    """Compare coverage information between original and mutant code."""
    # Track line changes per function
    new_lines = {}  # {func_name: line_numbers}
    removed_lines = {}  # {func_name: line_numbers}

    # Get sets of function signatures
    original_funcs = set(original_coverage.keys())
    mutant_funcs = set(mutant_coverage.keys())
    new_functions = list(mutant_funcs - original_funcs)
    removed_functions = list(original_funcs - mutant_funcs)

    # First handle completely new and removed functions
    for func in new_functions:
        info = mutant_coverage[func]
        new_lines[func] = sorted(info.get("lines", []))

    for func in removed_functions:
        info = original_coverage[func]
        removed_lines[func] = sorted(info.get("lines", []))

    # Then compare coverage for functions present in both
    common_funcs = original_funcs & mutant_funcs
    for func in common_funcs:
        orig_info = original_coverage[func]
        mutant_info = mutant_coverage[func]

        # Skip if source files don't match
        if orig_info["src"] != mutant_info["src"]:
            continue

        # Compare line coverage
        orig_lines = set(orig_info.get("lines", []))
        mutant_lines = set(mutant_info.get("lines", []))

        new_line_nums = mutant_lines - orig_lines
        removed_line_nums = orig_lines - mutant_lines

        if new_line_nums:
            new_lines[func] = sorted(list(new_line_nums))
        if removed_line_nums:
            removed_lines[func] = sorted(list(removed_line_nums))

    # Only return result if there are differences
    if new_lines or removed_lines:
        return {
            "new_functions": new_functions,
            "removed_functions": removed_functions,
            "new_lines": new_lines,
            "removed_lines": removed_lines,
        }
    return {}


def annotate_source_with_coverage(source_code: str, covered_lines: List[int]) -> str:
    """Add @VISITED annotations to covered lines."""
    lines = source_code.splitlines()
    annotated_lines = []

    for i, line in enumerate(lines, 1):
        if i in covered_lines:
            # Add @VISITED annotation, maintaining indentation
            # indent = len(line) - len(line.lstrip())
            # annotated_lines.append(" " * indent + "/* @VISITED */")
            line += " /* @VISITED */"
        annotated_lines.append(line)

    return "\n".join(annotated_lines)


def annotate_files_with_coverage(coverage_info: Dict) -> str:
    """Read source files and annotate them with coverage information."""
    # First, group coverage info by source file
    file_coverage: Dict[str, List[int]] = {}
    for func_name, info in coverage_info.items():
        src_path = info["src"]
        if src_path not in file_coverage:
            file_coverage[src_path] = []
        file_coverage[src_path].extend(info["lines"])

    # Remove duplicates and sort line numbers for each file
    for src_path in file_coverage:
        file_coverage[src_path] = sorted(list(set(file_coverage[src_path])))

    # Now process each file once
    annotated_sources = []
    for src_path, covered_lines in file_coverage.items():
        with open(src_path, "r") as f:
            source_code = f.read()

        annotated = annotate_source_with_coverage(source_code, covered_lines)
        annotated_sources.append(make_file_prompt(annotated, src_path))

    return "\n".join(annotated_sources)


def make_func_prompt(name: str, body: str, path: Optional[str] = None) -> str:
    """Format function code with name and path."""
    func_str = "<FUNCTION_INFO>\n"
    if path:
        func_str += f"<FILE_PATH>{path}</FILE_PATH>\n"
    func_str += f"<FUNC_NAME>{name}</FUNC_NAME>\n"
    func_str += "<FUNC_BODY>\n"
    func_str += f"{body}\n"
    func_str += "</FUNC_BODY>\n"
    func_str += "</FUNCTION_INFO>\n"
    return func_str


def make_file_prompt(code: str, path: Optional[str] = None) -> str:
    """Format function code with name and path."""
    func_str = "<SOURCE_CODE_INFO>\n"
    if path:
        func_str += f"<FILE_PATH>{path}</FILE_PATH>\n"
    func_str += "<SOURCE_CODE>\n"
    func_str += f"{code}\n"
    func_str += "</SOURCE_CODE>\n"
    func_str += "</SOURCE_CODE_INFO>\n"
    return func_str


def annotate_funcs_with_coverage(
    coverage_info: Dict, context_buffer: int = 5, max_line_number: int = 1000
) -> str:
    """Extract and annotate only function code based on coverage line ranges."""
    # Group functions by source file to avoid reading the same file multiple times
    file_functions: Dict[str, List[tuple]] = {}

    for func_name, info in coverage_info.items():
        src_path = info["src"]
        covered_lines = info["lines"]

        if not covered_lines:
            continue

        if src_path not in file_functions:
            file_functions[src_path] = []

        # Calculate function boundaries based on coverage
        min_line = min(covered_lines)
        max_line = max(covered_lines)
        start_line = max(1, min_line - context_buffer)
        end_line = max_line + context_buffer

        # Check if function exceeds max_line_number limit
        line_count = end_line - start_line + 1
        if line_count > max_line_number:
            logger.warning(
                f"Skipping function '{func_name}' in {src_path} "
                f"(lines {start_line}-{end_line}): {line_count} lines exceeds "
                f"max_line_number limit of {max_line_number}"
            )
            continue

        file_functions[src_path].append(
            (func_name, covered_lines, start_line, end_line)
        )

    # Process each file once
    annotated_functions = []
    for src_path, functions in file_functions.items():
        try:
            # Read the source file once
            with open(src_path, "r") as f:
                source_code = f.read()

            # Collect all covered lines for this file
            all_covered_lines = set()
            for _, covered_lines, _, _ in functions:
                all_covered_lines.update(covered_lines)

            # First, annotate the entire file with coverage using absolute line numbers
            annotated_full_source = annotate_source_with_coverage(
                source_code, sorted(all_covered_lines)
            )

            # Then instrument the entire file with absolute line numbers starting from 1
            instrumented_full_source, _ = instrument_line(annotated_full_source, 1)
            instrumented_lines = instrumented_full_source.splitlines()

            # Extract per-function code from the already-annotated lines
            for func_name, covered_lines, start_line, end_line in functions:
                # Ensure end_line doesn't exceed file length
                actual_end_line = min(len(instrumented_lines), end_line)

                # Extract function code (start_line is 1-based, but list is 0-based)
                function_lines = instrumented_lines[start_line - 1 : actual_end_line]
                function_code = "\n".join(function_lines)

                # Format as function prompt
                annotated_func = make_func_prompt(
                    name=func_name,
                    body=function_code,
                    path=f"{src_path} (lines {start_line}-{actual_end_line})",
                )
                annotated_functions.append(annotated_func)

        except Exception as e:
            logger.error(f"Failed to read file {src_path}: {e}")
            continue

    return "\n".join(annotated_functions)


def get_xxd(blob: bytes):
    import subprocess

    xxd_process = subprocess.run(["xxd", "-"], input=blob, capture_output=True)
    xxd_output = xxd_process.stdout.decode()

    return xxd_output
