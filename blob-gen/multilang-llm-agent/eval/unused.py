from pathlib import Path
from typing import Dict, List

import yaml

from eval.utils import logger


def copy_latest_results(mlla_path: str, results_dir: Path) -> None:
    """Copy latest mlla-result files from mlla_path/results to results_dir."""
    if results_dir:
        # Here, resultss_dir should be relative path.
        mlla_results = Path(mlla_path) / results_dir
    else:
        mlla_results = Path(mlla_path) / "results"
    results_dir = Path(results_dir).resolve()

    if not mlla_results.exists():
        logger.warning(f"No results directory found at {mlla_results}")
        return

    logger.info(f"Copying results from {mlla_results} to {results_dir}")

    # Group result files by target directory
    target_results: Dict[Path, List[Path]] = {}
    for result_file in mlla_results.rglob("mlla-result-*.yaml"):
        target_dir = result_file.parent
        if target_dir not in target_results:
            target_results[target_dir] = []
        target_results[target_dir].append(result_file)

    if not target_results:
        logger.warning("No result files found")
        return

    # Copy latest result file from each target directory
    for target_dir, files in target_results.items():
        try:
            # Sort by timestamp in filename (newest first)
            latest_file = sorted(files, key=lambda x: x.stem, reverse=True)[0]
            logger.info(f"Latest file in {target_dir}: {latest_file}")

            # Create relative path in our results directory
            rel_path = latest_file.relative_to(mlla_results)
            dst = results_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Read and write with UTF-8 encoding
            content = latest_file.read_text(encoding="utf-8")
            dst.write_text(content, encoding="utf-8")
            logger.info(f"Copied latest result from {target_dir} to {dst}")

        except Exception as e:
            logger.error(f"Error copying file from {target_dir}: {e}")
            continue

    logger.info(f"Copied results from {len(target_results)} target directories")


def get_harness_files(
    crs_multilang_path: str, targets: list[str]
) -> dict[tuple[str, str], dict[str, str]]:
    """Get all harness files for each target.
    Returns a dictionary mapping (target, harness_name) to a dict of CPV names
    and their error tokens.
    """
    projects_path = Path(crs_multilang_path) / "benchmarks" / "projects"
    result = {}

    for target in targets:
        target_path = projects_path / target
        aixcc_config_yaml = target_path / ".aixcc/config.yaml"

        with open(aixcc_config_yaml, "r") as f:
            config = yaml.safe_load(f)

        for harness in config.get("harness_files", []):
            if not isinstance(harness, dict):
                continue

            harness_name = harness.get("name")
            if not harness_name:
                continue

            cpv_dict = {}
            for cpv in harness.get("cpvs", []):
                if not isinstance(cpv, dict):
                    continue

                cpv_name = cpv.get("name")
                error_token = cpv.get("error_token")
                if cpv_name and error_token:
                    cpv_dict[cpv_name] = error_token

            if cpv_dict:
                result[(target, harness_name)] = cpv_dict

    return result
