from pathlib import Path
from typing import Optional, Set, Tuple

from eval.mlla_result import MLLAResult


def collect_harnesses_in_result(data: MLLAResult) -> Set[str]:
    """Collect all harness names from result data."""
    harnesses: Set[str] = set()
    if data.harness_status:
        harnesses.update(str(h) for h in data.harness_status)
    if data.sanitizer_results:
        for res in data.sanitizer_results.values():
            if isinstance(res, list):
                harnesses.update(r.harness for r in res)
    return harnesses


def parse_target_harness(
    file_path: Path, results_dir: Path
) -> Tuple[str, Optional[str]]:  # (target, harness)
    """Parse target and harness from directory path."""
    dir_name = str(file_path.parent.relative_to(results_dir))
    target_harness = dir_name.split("-")
    if len(target_harness) >= 2:
        harness = target_harness[-1]
        target = "-".join(target_harness[:-1])
    else:
        target = dir_name
        harness = None
    return target, harness
