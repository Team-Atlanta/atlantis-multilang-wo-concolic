"""Functions for running POV tests in Docker environment."""

import asyncio
import datetime
import hashlib
import json
import os
import tempfile
import traceback
from pathlib import Path

import yaml
from loguru import logger
from typing_extensions import Any, Dict, List, Optional, Tuple, TypedDict

from ..modules.sanitizer import Sanitizer
from .agent import (
    BCDA,
    BLOBGEN_AGENT,
    CGPA,
    CPUA,
    GENERATOR_AGENT,
    MCGA,
    MUTATOR_AGENT,
    ORCHESTRATOR_AGENT,
)
from .context import GlobalContext
from .coverage import cov_str_to_dict

# Global lock for all async_run_input calls
run_input_lock = asyncio.Lock()


class RunPovResult(TypedDict):
    """Result of process_pov_in_docker_async function."""

    blob_hash: str
    triggered: bool
    triggered_sanitizer: str
    stdout: str
    stderr: str
    coverage: Dict
    crash_log: str


def safe_decode(data: bytes) -> str:
    """Safely decode binary data to string, handling invalid UTF-8 sequences."""
    if not data:
        return ""
    try:
        return data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace").strip()


async def process_pov_in_docker_async(
    gc: GlobalContext,
    harness_name: str,
    blob_hash: str,
    blob_content: bytes,
    idx: int,
    total_povs: int,
) -> RunPovResult:
    """Process a single POV in docker environment."""
    result = RunPovResult(
        blob_hash=blob_hash,
        triggered=False,
        triggered_sanitizer="",
        stdout="",
        stderr="",
        coverage={},
        crash_log="",
    )

    if not blob_content:
        logger.warning(f"Empty blob content for harness {harness_name}")
        return result

    if harness_name not in gc._cp.harnesses:
        logger.error(f"Harness {harness_name} not found")
        return result

    logger.info(f"Running POV {idx}/{total_povs} for harness {harness_name}")
    output_truncate_size = 1024
    if len(blob_content) > output_truncate_size:
        logger.info(
            f"blob content ({len(blob_content)} bytes):"
            f" {str(blob_content)[:output_truncate_size]} ... (cut to"
            f" {output_truncate_size} bytes)"
        )
    else:
        logger.info(f"blob content ({len(blob_content)} bytes): {str(blob_content)}")

    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(blob_content)
            tmp_file.flush()
            logger.debug(f"Created temporary file: {tmp_file.name}")

            harness = gc._cp.harnesses[harness_name]
            async with run_input_lock:
                run_result = await harness.async_run_input(tmp_file.name)

        try:
            os.remove(tmp_file.name)
            logger.debug(f"Removed temporary file: {tmp_file.name}")
        except Exception as e:
            logger.error(f"Failed to remove temporary file {tmp_file.name}: {e}")

        stdout = safe_decode(run_result[0])
        stderr = safe_decode(run_result[1])
        coverage_str = safe_decode(run_result[2]) or "{}"
        crash_log = safe_decode(run_result[3])

        stderr = filter_fuzzing_output(stderr)
        oracle_str = stderr

        try:
            coverage = cov_str_to_dict(coverage_str)
        except json.JSONDecodeError:
            coverage = {}
            logger.error(f"Failed to parse coverage info for {blob_hash}")

        logger.debug(f"STDOUT: {stdout}")
        logger.debug(f"STDERR: {stderr}")
        logger.debug(f"CRASHLOG: {crash_log}")

        triggered, triggered_sanitizer = Sanitizer.detect_crash_type(oracle_str)
        if triggered:
            logger.info(
                f"POV {idx}/{total_povs} triggered {triggered_sanitizer} "
                f"for harness {harness_name}"
            )
            logger.info("Last 20 lines of crash_log:")
            last_few = "\n".join(crash_log.strip().split("\n")[-20:])
            logger.info(f"\n{last_few}")
        else:
            logger.debug(
                f"POV {idx}/{total_povs} did not trigger any sanitizer "
                f"for harness {harness_name}"
            )

        result = RunPovResult(
            blob_hash=blob_hash,
            triggered=triggered,
            triggered_sanitizer=triggered_sanitizer,
            stdout=stdout,
            stderr=stderr,
            coverage=coverage,
            crash_log=crash_log,
        )
        return result

    except Exception:
        error_msg = traceback.format_exc()
        logger.error(
            f"Failed to run POV {idx}/{total_povs} for {harness_name}: {error_msg}"
        )
        return result


def filter_fuzzing_output(stderr: str) -> str:
    """Filter out fuzzing output lines from stderr."""
    if not stderr:
        return ""

    filtered_lines = []
    for line in stderr.split("\n"):
        if "pulse" in line and "cov:" in line and "exec/s:" in line:
            continue
        filtered_lines.append(line)
    return "\n".join(filtered_lines).strip()


def log_execution_summary(
    results: List[RunPovResult],
    total_povs: int,
    start_time: datetime.datetime,
) -> None:
    """Log summary of POV execution results."""
    total_time = datetime.datetime.now() - start_time
    triggered_count = sum(1 for result in results if result["triggered"])

    sanitizer_counts: Dict[str, int] = {}
    for result in results:
        sanitizer_type = result["triggered_sanitizer"]
        if sanitizer_type:
            sanitizer_counts[sanitizer_type] = (
                sanitizer_counts.get(sanitizer_type, 0) + 1
            )

    summary = (
        f"POV execution completed in {total_time}:\n"
        f"   - Total POVs: {total_povs}\n"
        f"   - Triggered crashes: {triggered_count}\n"
    )

    if sanitizer_counts:
        summary += "   - Crashes by sanitizer:\n"
        for sanitizer, count in sorted(sanitizer_counts.items()):
            summary += f"      * {sanitizer}: {count}\n"

    if triggered_count > 0:
        logger.info(summary)
    else:
        logger.debug(summary)


def gen_result_report(
    succeeded: List[Tuple[str, Path, Optional[str]]],
    failed: List[Tuple[str, Path, Optional[str]]],
    gc: GlobalContext,
) -> None:
    """Generate result report for POV runs."""
    # Collect metrics
    metrics: Dict[str, Dict[str, Any]] = {
        "per_agent": {},
        "total": {
            "total_tokens": gc.general_callback.total_usage.total_tokens,
            "prompt_tokens": gc.general_callback.total_usage.prompt_tokens,
            "completion_tokens": gc.general_callback.total_usage.completion_tokens,
            "successful_requests": gc.general_callback.total_usage.requests,
            "total_cost": gc.general_callback.total_usage.cost,
            "execution_time": str(gc.get_execution_time()),
        },
    }

    agent_names = [
        CPUA,
        BCDA,
        MCGA,
        CGPA,
        BLOBGEN_AGENT,
        MUTATOR_AGENT,
        GENERATOR_AGENT,
        ORCHESTRATOR_AGENT,
    ]

    for agent_name in agent_names:
        usage = gc.general_callback.get_usage_between_snapshots(
            f"{agent_name}_start", f"{agent_name}_end"
        )
        if usage:
            env_prefix = agent_name.upper()
            model = os.getenv(f"{env_prefix}_MODEL", "")
            temperature = float(os.getenv(f"{env_prefix}_TEMPERATURE", 0))

            metrics["per_agent"][agent_name] = {
                "model": model,
                "temperature": temperature,
                "total_tokens": usage.total_usage.total_tokens,
                "prompt_tokens": usage.total_usage.prompt_tokens,
                "completion_tokens": usage.total_usage.completion_tokens,
                "successful_requests": usage.total_usage.requests,
                "total_cost": usage.total_usage.cost,
                "execution_time": str(usage.duration) if usage.duration else None,
            }

    # Initialize result structure
    result: Dict[str, Any] = {
        "sanitizer_results": dict(),
        "harness_status": dict(),
        "blob_stats": {
            "total": len(succeeded) + len(failed),
            "succeeded": len(succeeded),
            "failed": len(failed),
        },
        "llm_metrics": metrics,
    }

    # Get all harnesses
    all_harnesses = set([h for h, _, _ in succeeded + failed])
    if not all_harnesses:
        all_harnesses = set(gc.cp.harnesses.keys())

    # Process blob results
    harness_results: Dict[str, Dict[str, List[Path]]] = {
        h: {"succeeded": [], "failed": []} for h in all_harnesses
    }
    exploited_harnesses: set = set()

    # Process succeeded blobs
    for harness_name, blob_name, sanitizer in succeeded:
        logger.info(f"Succeeded: {harness_name}: {blob_name}, {sanitizer}")
        harness_results[harness_name]["succeeded"].append(blob_name)
        exploited_harnesses.add(harness_name)

        if sanitizer:
            if sanitizer not in result["sanitizer_results"]:
                result["sanitizer_results"][sanitizer] = []
            result["sanitizer_results"][sanitizer].append(
                {"harness": harness_name, "blob": str(blob_name)}
            )

    # Process failed blobs
    for harness_name, blob_name, _ in failed:
        logger.debug(f"Failed: {harness_name}: {blob_name}")
        harness_results[harness_name]["failed"].append(blob_name)

    # Set harness status
    for harness in all_harnesses:
        succeeded_blobs = harness_results.get(harness, {"succeeded": [], "failed": []})[
            "succeeded"
        ]
        failed_blobs = harness_results.get(harness, {"succeeded": [], "failed": []})[
            "failed"
        ]

        result["harness_status"][harness] = {
            "exploited": harness in exploited_harnesses,
            "total_blobs": len(succeeded_blobs) + len(failed_blobs),
            "successful_blobs": len(succeeded_blobs),
        }

    # Log summary statistics
    logger.info(f"Total blobs: {result['blob_stats']['total']}")
    logger.info(f"Succeeded: {result['blob_stats']['succeeded']}")
    logger.info(f"Failed: {result['blob_stats']['failed']}")
    logger.info(f"Total harnesses: {len(all_harnesses)}")
    logger.info(f"Exploited harnesses: {len(exploited_harnesses)}/{len(all_harnesses)}")

    for sanitizer, blobs in result["sanitizer_results"].items():
        logger.info(f"{sanitizer} sanitizer hits: {len(blobs)}")

    # Write result to file
    try:
        with open(gc.RESULT_FILE, "w") as f:
            yaml.dump(result, f)
    except OSError as e:
        logger.error(f"Error writing results: {e}")


def get_latest_blob_dir(gc: GlobalContext) -> Optional[Path]:
    """Find the latest blob directory."""
    if not gc.BLOBS_DIR.exists():
        logger.error(f"Blobs directory does not exist: {gc.BLOBS_DIR}")
        return None

    blob_dirs = sorted(
        [d for d in gc.BLOBS_DIR.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )

    if not blob_dirs:
        logger.error(f"No blob directories found in {gc.BLOBS_DIR}")
        return None

    latest_dir = blob_dirs[0]
    logger.info(f"Using latest blob directory: {latest_dir}")
    return latest_dir


def load_results_from_blob_metadata(
    directories: List[Path],
    harness_name: str,
) -> tuple[list, list, list, dict]:
    """Handle blobs that have metadata and queue others for execution."""
    from .artifact_storage import load_artifact_metadata

    succeeded = []
    failed = []
    to_run = []
    blob_paths = {}

    for directory in directories:
        logger.info(f"Processing directory: {directory}")

        for blob_path in sorted(directory.rglob("*.blob")):
            try:
                metadata = load_artifact_metadata(blob_path)

                if metadata:
                    # Process blob with metadata
                    run_pov_result = metadata.get("run_pov_result", {})
                    triggered = run_pov_result.get("triggered", False)
                    triggered_sanitizer = run_pov_result.get("triggered_sanitizer", "")

                    if triggered:
                        logger.debug(
                            f"Blob {blob_path.name} triggered {triggered_sanitizer} for"
                            f" harness {harness_name} (from metadata)"
                        )
                    else:
                        logger.debug(
                            f"Blob {blob_path.name} did not trigger any sanitizer for"
                            f" harness {harness_name} (from metadata)"
                        )

                    result_tuple = (harness_name, blob_path, triggered_sanitizer)
                    if triggered_sanitizer:
                        succeeded.append(result_tuple)
                    else:
                        failed.append(result_tuple)
                else:
                    # Queue blob for execution
                    try:
                        with open(blob_path, "rb") as f:
                            blob_content = f.read()

                        if not blob_content:
                            logger.warning(f"Empty blob found: {blob_path}")
                            continue

                        blob_hash = hashlib.md5(blob_content).hexdigest()
                        logger.debug(f"Queued blob for execution: {blob_path}")
                        to_run.append((harness_name, blob_hash, blob_content))
                        blob_paths[(harness_name, blob_content)] = blob_path
                    except Exception as e:
                        logger.error(f"Failed to read blob {blob_path}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Failed to process blob {blob_path}: {e}")
                continue

    return succeeded, failed, to_run, blob_paths


async def run_pov_blobs(
    gc: GlobalContext, to_run: list, blob_paths: dict
) -> tuple[list, list]:
    """Execute POV tests on blobs and process results."""
    succeeded: List = []
    failed: List = []

    if not to_run:
        return succeeded, failed

    logger.info(f"Running POV tests on {len(to_run)} blobs without metadata")

    try:
        total_povs = len(to_run)
        start_time = datetime.datetime.now()

        logger.debug(f"Starting POV execution for {total_povs} POVs in Docker")

        results = []
        logger.debug("Running POVs in docker")

        for idx, (harness_name, blob_hash, blob_content) in enumerate(to_run, 1):
            result = await process_pov_in_docker_async(
                gc, harness_name, blob_hash, blob_content, idx, total_povs
            )
            results.append(result)

        log_execution_summary(results, total_povs, start_time)

        # Process execution results
        for (harness_name, _, blob_content), result in zip(to_run, results):
            blob_path = blob_paths[(harness_name, blob_content)]
            triggered = result["triggered"]
            triggered_sanitizer = result["triggered_sanitizer"]

            if triggered:
                logger.debug(
                    f"Blob {blob_path.name} triggered {triggered_sanitizer} for harness"
                    f" {harness_name} (executed)"
                )
            else:
                logger.debug(
                    f"Blob {blob_path.name} did not trigger any sanitizer for harness"
                    f" {harness_name} (executed)"
                )

            result_tuple = (harness_name, blob_path, triggered_sanitizer)
            if triggered:
                succeeded.append(result_tuple)
            else:
                failed.append(result_tuple)

    except Exception as e:
        logger.error(f"Error running POV tests: {e}")
        raise

    return succeeded, failed


async def run_pov_and_check(
    gc: GlobalContext,
    blob_dir: Path | None = None,
    run_blobs: bool = False,
) -> tuple[list, list]:
    """Run POV tests on all blobs in blobs and crash directories."""
    succeeded: list[tuple[str, Path, Optional[str]]] = []
    failed: list[tuple[str, Path, Optional[str]]] = []

    # Determine which directories to search
    dirs_to_search = []

    if blob_dir is not None:
        # If a specific directory is provided, only search in that directory
        if blob_dir.exists():
            dirs_to_search.append(blob_dir)
            logger.info(f"Using specified directory: {blob_dir}")
        else:
            logger.error(f"Specified directory does not exist: {blob_dir}")
    else:
        # If no directory is provided, search in both crash and blobs directories
        if gc.CRASH_TIMESTAMP_DIR.exists():
            dirs_to_search.append(gc.CRASH_TIMESTAMP_DIR)
            logger.info(f"Including crash directory: {gc.CRASH_TIMESTAMP_DIR}")

        if gc.BLOBS_TIMESTAMP_DIR.exists():
            dirs_to_search.append(gc.BLOBS_TIMESTAMP_DIR)
            logger.info(f"Including blobs directory: {gc.BLOBS_TIMESTAMP_DIR}")

    if not dirs_to_search:
        logger.warning("No valid directories found, generating empty report")
        gen_result_report([], [], gc)
        return [], []

    # Handle metadata blobs and queue others for execution
    harness_name = gc.target_harness
    meta_succeeded, meta_failed, to_run, blob_paths = load_results_from_blob_metadata(
        dirs_to_search, harness_name
    )
    succeeded.extend(meta_succeeded)
    failed.extend(meta_failed)

    # Execute blobs that don't have metadata (only if run_blobs is True)
    if to_run:
        if run_blobs:
            exec_succeeded, exec_failed = await run_pov_blobs(gc, to_run, blob_paths)
            succeeded.extend(exec_succeeded)
            failed.extend(exec_failed)
        else:
            logger.info(
                f"Skipping execution of {len(to_run)} blobs without metadata"
                " (run_blobs=False)"
            )
            # Add these blobs to the failed list since we're not running them
            for harness_name, _, blob_content in to_run:
                blob_path = blob_paths[(harness_name, blob_content)]
                result_tuple = (harness_name, blob_path, "")
                logger.debug(
                    f"Skipped blob {blob_path.name} for harness {harness_name}"
                )
                failed.append(result_tuple)
    else:
        logger.info("No blobs needed execution (all had metadata)")

    # Generate and save report
    logger.debug("Generating POV test report")
    gen_result_report(succeeded, failed, gc)

    return succeeded, failed
