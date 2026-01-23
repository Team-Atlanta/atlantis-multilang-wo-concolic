from typing import Dict, List


def merge_coverage(coverage_results: List[Dict]) -> Dict:
    """Merge coverage information from multiple results."""
    merged_coverage = {}
    for result in coverage_results:
        coverage_info = result.get("coverage_info", {})
        for func_name, info in coverage_info.items():
            if func_name not in merged_coverage:
                merged_coverage[func_name] = info.copy()
            else:
                # Merge line coverage
                existing_lines = set(merged_coverage[func_name].get("lines", []))
                new_lines = set(info.get("lines", []))
                merged_coverage[func_name]["lines"] = sorted(
                    list(existing_lines.union(new_lines))
                )

    return merged_coverage


