"""Functions for running POV tests outside of Docker environment."""

# This file is deprecated as we only consider in-docker environment.
# mypy: ignore-errors

import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
from loguru import logger

from ..modules.sanitizer import Sanitizer
from .context import GlobalContext


def clean_info_lines(input_string: str) -> str:
    """Remove INFO lines and clean up extra newlines"""
    cleaned = re.sub(r"^INFO: .*$", "", input_string, flags=re.MULTILINE)
    return re.sub(r"\n+", "\n", cleaned).strip()


def set_process_affinity(pid: int) -> None:
    """Set process affinity to least used CPU core"""
    try:
        core_usages = psutil.cpu_percent(interval=0.2, percpu=True)
        core_num = core_usages.index(min(core_usages))
        p = psutil.Process(pid)
        p.cpu_affinity([core_num])
        logger.debug(f"Process {pid} allocated to core {core_num}")
    except psutil.Error as e:
        logger.error(f"Error setting CPU affinity: {e}")


def check_docker_image_exists(image_name: str) -> bool:
    """Check if a Docker image exists locally."""
    try:
        result = subprocess.run(
            f"docker images -q {image_name}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            bufsize=1,
            shell=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def check_fuzzer_binary_exists(
    oss_fuzz_build: Path, target_path: str, fuzzer_name: str
) -> bool:
    """Check if a specific fuzzer binary exists and is executable."""
    fuzzer_path = oss_fuzz_build / target_path / fuzzer_name
    return fuzzer_path.is_file() and os.access(fuzzer_path, os.X_OK)


def build_docker_images(
    gc: GlobalContext, fuzz_target_binary: str, blob_name: str | None = None
):
    """Build necessary Docker images and fuzzers for running POVs."""
    logger.info("Building Docker images...")

    try:
        # Step 1: Build base images if needed
        base_images_script = gc.oss_fuzz_path / "infra/base-images/multilang-all.sh"
        if not check_docker_image_exists("gcr.io/oss-fuzz-base/base-builder"):
            logger.info("Building base images... this may take a lot ...")
            subprocess.run(
                str(base_images_script),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                check=True,
                shell=True,
            )
        else:
            logger.info("Base images already exist, skipping build")

        # Step 2: Build crs-multilang if needed
        if not check_docker_image_exists("crs-multilang"):
            logger.info("Building crs-multilang image... this takes a lot ...")
            subprocess.run(
                f"docker build -t crs-multilang {gc.crs_multilang_path}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                check=True,
                shell=True,
            )
        else:
            logger.info("crs-multilang image already exists, skipping build")

        # Step 3: Check and build fuzzers if needed
        if not check_fuzzer_binary_exists(
            gc.oss_fuzz_build, gc.target_path, fuzz_target_binary
        ):
            logger.info("Building fuzzers...")
            cmd = f"python3 {gc.helper_script} build_fuzzers"
            cmd += f" --crs-multilang {gc.target_path} {gc.cp.cp_src_path}"
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                check=True,
                shell=True,
            )
            logger.info("Fuzzers built successfully")

            # Verify build was successful
            if not check_fuzzer_binary_exists(
                gc.oss_fuzz_build, gc.target_path, fuzz_target_binary
            ):
                raise FileNotFoundError(
                    f"Fuzzer binary {fuzz_target_binary} not found after build"
                )
        else:
            logger.info("Fuzzers already built and up to date, skipping build")

        logger.info("Preparing commands ...")
        if not blob_name:
            # If no blob_name provided, we're just building fuzzers
            return []
        else:
            cmd = f"python3 {gc.helper_script} reproduce"
            cmd += f" {gc.target_path} {fuzz_target_binary} {blob_name}"
            return cmd
    except subprocess.CalledProcessError as e:
        logger.error(f"Build failed: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during build: {e}")
        raise


def run_pov(gc: GlobalContext, harness_name: str, blob_name: str) -> list[str]:
    """Run a POV (Proof of Vulnerability) test case outside docker."""
    logger.info(f"Running POV for {harness_name} with blob {blob_name}")

    # assert not is_running_in_docker(), "This should be run outside of the docker"

    command = (
        f"python3 {gc.helper_script} reproduce"
        f" {gc.target_path} {harness_name} {blob_name}"
    ).split()

    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=gc.cp.proj_path,
                stdin=subprocess.DEVNULL,  # <-- this will solve the mangling
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,  # Line buffered
                shell=False,  # Let shell handle process management
                preexec_fn=os.setsid,
            )

            set_process_affinity(process.pid)
            logger.debug(f"Process started with PID {process.pid}")

            stdout, stderr = process.communicate(timeout=60)  # 60 second timeout

            if process.returncode != 0:
                logger.warning(
                    f"Process {process.pid} exited with non-zero code:"
                    f" {process.returncode}"
                )

        except subprocess.TimeoutExpired:
            process.kill()
            logger.error(f"Process {process.pid} timed out after 60 seconds")
            return ["", "Process timed out"]

    except subprocess.SubprocessError as e:
        logger.error(f"Failed to run command: {e}")
        return ["", f"Failed to run command: {e}"]
    except Exception as e:
        logger.error(f"Unexpected error running POV: {e}")
        return ["", f"Unexpected error: {e}"]

    # Combine and return output
    stdout = stdout.strip()
    stderr = stderr.strip()

    return [stdout, stderr]


def run_pov_list_outside_docker(
    gc: GlobalContext, to_run: list[tuple[str, str, bytes]]
) -> list[tuple[str, bool, str | None, list[str]]]:
    """Run POV tests in parallel outside docker environment."""
    results: list[tuple[str, bool, str | None, list[str]]] = []
    max_workers = os.cpu_count() or 1
    logger.info(f"Running POVs in parallel with {max_workers} workers")

    def execute_pov_temporary(
        args: tuple[str, str, bytes],
    ) -> tuple[str, bool, str | None, list[str]]:
        """Execute a single POV test with proper resource management"""
        harness_name, blob_hash, blob_content = args

        # Input validation
        if not blob_content:
            logger.warning(f"Empty blob content for harness {harness_name}")
            return blob_hash, False, None, []

        if harness_name not in gc._cp.harnesses:
            logger.error(f"Harness {harness_name} not found")
            return blob_hash, False, None, []

        logger.debug(f"Running POV for harness {harness_name}")

        try:
            # Create temporary file
            tmp_file = tempfile.NamedTemporaryFile(delete=False)
            tmp_file.write(blob_content)
            tmp_file.flush()
            tmp_file.close()
            logger.debug(f"Created temporary file: {tmp_file.name}")

            # Run outside docker
            run_result = run_pov(gc, harness_name, tmp_file.name)

            # Clean up temporary file early
            try:
                os.remove(tmp_file.name)
                logger.debug(f"Removed temporary file: {tmp_file.name}")
            except Exception as e:
                logger.error(f"Failed to remove temporary file {tmp_file.name}: {e}")

            # Process results
            stdout = run_result[0].strip() if run_result[0] else ""
            stdout = clean_info_lines(stdout)
            stderr = run_result[1].strip() if run_result[1] else ""
            coverage = ""
            crash_log = "No crash log outside of the docker"
            oracle_str = "\n".join([stdout, stderr])

            logger.debug(f"STDOUT: {stdout}")
            logger.debug(f"STDERR: {stderr}")
            logger.debug(f"CRASHLOG: {crash_log}")

            # Crash detection
            triggered, triggered_sanitizer = Sanitizer.detect_crash_type(oracle_str)
            if triggered:
                logger.info(
                    f"POV triggered {triggered_sanitizer} for harness {harness_name}"
                )
                logger.debug("Last 20 lines of crash_log:")
                last_few = "\n".join(crash_log.strip().split("\n")[-20:])
                logger.debug(f"\n{last_few}")
            else:
                logger.debug(f"POV did not trigger any sanitizer for {harness_name}")

            return (
                blob_hash,
                triggered,
                triggered_sanitizer,
                [stdout, stderr, coverage, crash_log],
            )

        except Exception as e:
            logger.error(f"Failed to run POV for {harness_name}: {e}")
            return blob_hash, False, None, []

    # Execute POVs in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = list(executor.map(execute_pov_temporary, to_run))
        results.extend(futures)

    return results
