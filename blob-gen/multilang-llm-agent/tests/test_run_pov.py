#!/usr/bin/env python3
import argparse
import asyncio
import datetime
import sys
from pathlib import Path

from loguru import logger

from mlla.utils.context import GlobalContext
from mlla.utils.run_pov import run_pov_and_check


class DummyContext(GlobalContext):
    def __init__(
        self,
        cp_path: Path,
        target_harness: str,
        crs_multilang_path: str | None = None,
        workdir: str = "results",
        output_dir: str | None = None,
    ):
        self.is_dev = True
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._init_cp(cp_path, target_harness)
        # self._init_vars_outside_docker(crs_multilang_path)
        self._init_directories(workdir, output_dir, target_harness)

        self.target_harness = target_harness


async def main():
    parser = argparse.ArgumentParser(description="Run POV tests on blobs")
    parser.add_argument(
        "--cp", type=str, required=True, help="Path to the CP directory"
    )
    parser.add_argument(
        "--harness",
        type=str,
        required=True,
        help="Harness name to run the POV tests for",
    )
    parser.add_argument("blob_dir", help="Path to the blob directory")
    parser.add_argument(
        "--crs-multilang-path",
        type=str,
        help=(
            "Path to CRS-multilang repository. If not provided, will try to detect it."
        ),
    )
    args = parser.parse_args()

    # Initialize DummyContext with minimal required parameters
    gc = DummyContext(
        cp_path=Path(args.cp),
        target_harness=args.harness,
        crs_multilang_path=args.crs_multilang_path,
    )

    # Get full path and verify it's a directory
    blob_dir = Path(args.blob_dir).resolve()
    if not blob_dir.exists() or not blob_dir.is_dir():
        logger.error(f"Invalid blob directory: {blob_dir}")
        sys.exit(1)

    # Run POV tests using run_pov_and_check
    logger.info(f"Running POV tests for harness {args.harness} on blobs in: {blob_dir}")
    succeeded, failed = await run_pov_and_check(gc, blob_dir)

    # Filter results for the specified harness
    succeeded = [(h, p, s) for h, p, s in succeeded if h == args.harness]
    failed = [(h, p, s) for h, p, s in failed if h == args.harness]

    # Print summary
    total = len(succeeded) + len(failed)
    logger.info("Test Summary:")
    logger.info(f"Total blobs tested: {total}")
    logger.info(f"Succeeded: {len(succeeded)}")
    logger.info(f"Failed: {len(failed)}")

    # Print details of succeeded tests
    if succeeded:
        logger.info("\nSucceeded Tests:")
        for harness_name, blob_path, sanitizer in succeeded:
            logger.info(f"- {harness_name}: {blob_path.name} (Triggered: {sanitizer})")

    # Print details of failed tests
    if failed:
        logger.info("\nFailed Tests:")
        for harness_name, blob_path, _ in failed:
            logger.info(f"- {harness_name}: {blob_path.name}")

    # Exit with success if any test succeeded
    sys.exit(0 if succeeded else 1)


if __name__ == "__main__":
    asyncio.run(main())
