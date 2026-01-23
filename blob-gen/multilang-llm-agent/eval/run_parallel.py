#!/usr/bin/env python3

import argparse
import datetime
import getpass
import multiprocessing
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

from libCRS.challenge import CP
from loguru import logger

from eval.print_result import print_results_summary
from eval.result import group_result_files
from eval.utils import setup_logger


def get_crash_logs(
    crs_multilang_path: str, targets: list[str]
) -> dict[tuple[str, str], dict[str, Path]]:
    """Get all crash logs for each target.
    Returns a dictionary mapping (target, crash_log_dir_name) to a dict of cpv
    names and their log file paths.
    """
    projects_path = Path(crs_multilang_path) / "benchmarks" / "projects"
    result = {}

    for target in targets:
        target_path = projects_path / target
        crash_logs_path = target_path / ".aixcc/crash_logs"

        if not crash_logs_path.exists():
            continue

        for crash_dir in crash_logs_path.iterdir():
            if not crash_dir.is_dir():
                continue

            cpv_dict = {}
            for log_file in crash_dir.glob("*.log"):
                cpv_name = log_file.stem
                cpv_dict[cpv_name] = log_file

            if cpv_dict:
                result[(target, crash_dir.name)] = cpv_dict

    return result


def find_targets(crs_multilang_path: str) -> List[str]:
    """Find all target directories within the benchmarks/projects directory."""
    projects_path = Path(crs_multilang_path) / "benchmarks" / "projects"
    if not projects_path.is_dir():
        logger.error(f"Projects directory not found: {projects_path}")
        sys.exit(1)

    targets = []
    for root, _, _ in os.walk(projects_path):
        root_path = Path(root)
        if ".aixcc" not in root_path.name:
            continue
        root_path = root_path.parent
        # Remove the prefix to get the target name
        target = str(root_path).replace(str(projects_path) + "/", "")
        targets.append(target)

    return targets


def read_targets_from_file(file_path: str) -> List[str]:
    """Read targets from a file, one target per line."""
    try:
        with open(file_path, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error(f"Target file not found: {file_path}")
        sys.exit(1)


def get_harnesses(target: str, crs_multilang_path: str) -> List[str]:
    """Get list of harnesses for a given target using CP."""
    projects_path = Path(crs_multilang_path) / "benchmarks" / "projects"
    target_path = projects_path / target

    # Initialize CP with target info
    cp = CP(
        name=target,
        proj_path=str(target_path),
        cp_src_path=str(target_path / "src"),
        built_path=None,
    )

    # Get harness names from CP
    return [harness.name for harness in cp.harnesses.values()]


def run_command(
    params: Dict[str, str],
    start_timestamp: str,
    mlla_path: str,
    no_harness: bool,
    results_dir: str,
    crs_multilang_path: str,
):
    """Run the command with given parameters."""
    target = params["target"]
    harness = params["harness"]
    # Get unique results directory for this model
    # logger.info(f"Using results directory for {model}: {results_dir_base}")

    cmd = [
        "./bin/run_mlla_eval",
        crs_multilang_path,  # crs_multilang_path
        target,  # CP
        "--",
        # "--workdir",
        # results_dir,
    ]

    # Add harness parameter only if not in no-harness mode
    if not no_harness:
        cmd.extend(["--harness", harness])

    cmd.append("--eval")

    # Create results directory
    if no_harness:
        output_dir = Path(results_dir) / target
    else:
        # Use <target>-<harness> directory structure
        output_dir = Path(results_dir) / f"{target}-{harness}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Define output files
    output_base = output_dir / start_timestamp
    stdout_file = output_base.with_suffix(".stdout.txt")
    stderr_file = output_base.with_suffix(".stderr.txt")
    new_env = os.environ.copy()
    litellm_key = (
        getpass.getpass("Enter your LiteLLM API key: ").strip()
        if os.getenv("LITELLM_KEY") is None
        else os.getenv("LITELLM_KEY", default="")
    )
    new_env["LLM_KEY"] = litellm_key

    try:
        if no_harness:
            logger.info(f"Running command for target: {target}")
        else:
            logger.info(f"Running command for target: {target}, harness: {harness}")
        # Start process with Popen
        logger.info(f"Running command: {' '.join(cmd)}")

        process = subprocess.Popen(
            # ' '.join(cmd),
            cmd,
            # cwd=crs_multilang_path,
            stdin=subprocess.DEVNULL,  # <-- this will solve the mangling
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # text=True,
            encoding="utf-8",
            # errors="replace",
            # bufsize=1,  # Line buffered
            shell=False,
            # preexec_fn=os.setsid,
            env=new_env,
        )

        if process.stdout is None or process.stderr is None:
            logger.error("Process stdout or stderr is None")
            return

        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)

        with open(stdout_file, "a", encoding="utf-8") as stdout_f, open(
            stderr_file, "a", encoding="utf-8"
        ) as stderr_f:

            poller = select.poll()
            poller.register(process.stdout, select.POLLIN)
            poller.register(process.stderr, select.POLLIN)

            fd_to_file = {
                process.stdout.fileno(): stdout_f,
                process.stderr.fileno(): stderr_f,
            }

            while True:
                events = poller.poll(100)  # timeout 100ms
                for fd, event in events:
                    if event & select.POLLIN:
                        data = os.read(fd, 1024)  # Buffer size 1024 bytes
                        if data:
                            fd_to_file[fd].write(data.decode("utf-8"))
                            fd_to_file[fd].flush()

                if process.poll() is not None:
                    # After process termination, read remaining data
                    for fd_io in [process.stdout, process.stderr]:
                        remaining = fd_io.read()
                        if remaining:
                            fd_to_file[fd_io.fileno()].write(remaining)
                            fd_to_file[fd_io.fileno()].flush()
                    break

        ret_code = process.wait()

        if ret_code != 0:
            if no_harness:
                logger.error(f"Command failed for {target}")
            else:
                logger.error(f"Command failed for {target}_{harness}")
            logger.error(f"Error output written to: {stderr_file}")
        else:
            if no_harness:
                logger.info(f"Successfully completed {target}")
            else:
                logger.info(f"Successfully completed {target}_{harness}")
            logger.info(f"Output written to: {stdout_file}")

    except KeyboardInterrupt:
        ret_code = process.wait()
        logger.error(f"Command returned with code: {ret_code}")
        # raise

    except Exception as e:
        logger.error(f"Error running command: {e}")
        # Write error to stderr file
        stderr_file.write_text(str(e), encoding="utf-8")


def get_unique_results_dir(
    base_path: str, model_name: str | None, temperature: float | None
) -> str:
    """Get results directory path for model and temperature."""
    if model_name is None:
        model_name = ""
    if temperature is None:
        temp_str = ""
    else:
        # Format temperature with 1 decimal place, remove trailing zeros
        temp_str = f"{temperature:.1f}".rstrip("0").rstrip(".")

    # Create and return base name without any index suffix
    return f"{base_path}_{model_name}_t{temp_str}"


def setup_argument_parser() -> argparse.ArgumentParser:
    """Set up and return the argument parser for command line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Run commands in parallel for multiple targets, harnesses, and models"
        )
    )
    parser.add_argument("--target-file", help="File containing list of targets")
    parser.add_argument(
        "--crs-multilang-path",
        default=".",
        help=(
            "Path to CRS-Multilang repository to automatically find targets (default:"
            " current directory)"
        ),
    )
    parser.add_argument("--mlla-path", required=True, help="Path to MLLA repository")
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=multiprocessing.cpu_count(),
        help="Maximum number of parallel processes (default: number of CPU cores)",
    )
    parser.add_argument(
        "--no-harness",
        action="store_true",
        help="Run without harness parameter",
    )
    parser.add_argument(
        "--results",
        default="results",
        help="Directory to store results (default: results)",
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Directory to store output csv file (default: .)",
    )
    parser.add_argument(
        "--print-results",
        action="store_true",
        help="Print summary of results from output files",
    )
    return parser


def prepare_parameters(args, targets: list) -> tuple[list, dict]:
    """Prepare parameters for all combinations of models, temperatures and targets."""

    all_params = []
    num_harnesses = {}

    def _add_params_for_target(target, args, harnesses=None):
        """Helper function to add parameters for a target with optional model
        and temperature."""
        if args.no_harness:
            params = {"target": target}
            return [params]

        if harnesses is None:
            harnesses = get_harnesses(target, args.crs_multilang_path)

        params_list = []
        for harness in harnesses:
            params = {"target": target, "harness": harness}
            params_list.append(params)

        return params_list, len(harnesses)

    # Handle case when both models and temperatures are empty
    for target in targets:
        params, harness_count = _add_params_for_target(target, args)
        all_params.extend(params)
        if not args.no_harness:
            num_harnesses[target] = harness_count

    return all_params, num_harnesses


def group_params_by_target(all_params: list) -> dict:
    """Group parameters by target for parallel processing."""
    target_groups: dict[str, list[dict]] = {}
    for params in all_params:
        target = params["target"]
        if target not in target_groups:
            target_groups[target] = []
        target_groups[target].append(params)
    return target_groups


def process_completed_task(
    active_tasks: list,
    i: int,
    target_groups: dict,
    available_targets: list,
    target_index: int,
) -> int:
    """Process a completed task and update relevant tracking structures."""
    target, task, params = active_tasks[i]
    try:
        task.get()
        logger.info(f"Completed task for target {target}: {params}")

        target_groups[target].pop(0)

        if not target_groups[target]:
            del target_groups[target]
            available_targets.remove(target)
            if available_targets:
                target_index = target_index % len(available_targets)
    except Exception as e:
        logger.error(f"Task failed for target {target}: {e}")

    active_tasks.pop(i)
    return target_index


def run_parallel_tasks(pool, args, all_params: list, start_timestamp_str: str):
    """Run tasks in parallel while maintaining target-level sequencing."""
    target_groups = group_params_by_target(all_params)
    logger.info(f"Target groups: {target_groups}")
    active_tasks: list[tuple[str, multiprocessing.pool.ApplyResult, dict]] = []
    available_targets = list(target_groups.keys())
    target_index = 0

    while target_groups:
        if len(active_tasks) < args.max_parallel and available_targets:
            target = available_targets[target_index]
            target_index = (target_index + 1) % len(available_targets)

            if not any(t[0] == target for t in active_tasks):
                params = target_groups[target][0]
                task = pool.apply_async(
                    run_command,
                    (
                        params,
                        start_timestamp_str,
                        args.mlla_path,
                        args.no_harness,
                        args.results,
                        args.crs_multilang_path,
                    ),
                )
                active_tasks.append((target, task, params))
                logger.info(f"Started task for target {target}: {params}")

        for i in range(len(active_tasks) - 1, -1, -1):
            if active_tasks[i][1].ready():
                target_index = process_completed_task(
                    active_tasks, i, target_groups, available_targets, target_index
                )

        if active_tasks:
            time.sleep(0.1)


def setup_execution_environment() -> tuple[datetime.datetime, str]:
    """Set up the initial execution environment including timestamps."""
    start_timestamp = datetime.datetime.now()
    start_timestamp_str = start_timestamp.strftime("%Y%m%d_%H%M%S")
    return start_timestamp, start_timestamp_str


def process_targets(args: argparse.Namespace) -> list[str]:
    """Process and validate targets from input arguments."""
    targets = (
        read_targets_from_file(args.target_file)
        if args.target_file
        else find_targets(args.crs_multilang_path)
    )
    logger.info(f"Found {len(targets)} targets")
    return targets


def get_processed_targets(results_dir: Path) -> list[str]:
    """Process and extract unique targets from result files."""

    def _process_target(target: Path) -> str:
        target_name = target.relative_to(results_dir).as_posix()
        if len(target_name.split("-")) == 2:
            return target_name.split("-")[0]
        return "-".join(target_name.split("-")[:-1])

    return list(set(map(_process_target, group_result_files(results_dir).keys())))


def initializer():
    # signal.signal(signal.SIGINT, signal.SIG_IGN)
    pass


def execute_parallel_tasks(
    args: argparse.Namespace, all_params: list, start_timestamp_str: str
) -> None:
    """Execute tasks in parallel using a process pool."""
    pool = None
    try:
        pool = multiprocessing.Pool(
            processes=args.max_parallel, initializer=initializer
        )
        run_parallel_tasks(pool, args, all_params, start_timestamp_str)
    except KeyboardInterrupt:
        logger.warning("Main process detected KeyboardInterrupt. Terminating pool...")
        if pool:
            pool.terminate()
            pool.join()
            logger.info("All subprocesses have been terminated.")
        raise
    finally:
        if pool:
            pool.close()
            pool.join()


def main():
    """Main entry point for parallel execution."""
    # Setup
    setup_logger()
    start_timestamp, start_timestamp_str = setup_execution_environment()
    args = setup_argument_parser().parse_args()

    # Process targets
    targets = process_targets(args)
    results_dir = Path(args.results)
    processed_targets = get_processed_targets(results_dir)
    crash_logs = get_crash_logs(args.crs_multilang_path, processed_targets)

    # Handle print results mode
    if args.print_results:
        logger.info(f"Targets: {get_processed_targets(Path(args.results))}")
        print_results_summary(
            args.results,
            crash_logs,
            output_dir=args.output,
        )
        return

    # Prepare and execute parallel tasks
    all_params, num_harnesses = prepare_parameters(args, targets)

    logger.info(f"Running {len(all_params)} combinations for {len(targets)} targets.")
    logger.info(f"All params: {all_params}")
    logger.info(f"Total {sum(num_harnesses.values())} harnesses")

    execute_parallel_tasks(args, all_params, start_timestamp_str)

    # Print final results
    execution_time = datetime.datetime.now() - start_timestamp
    logger.info(f"Completed. Took {execution_time}")
    print_results_summary(
        args.results,
        crash_logs,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
