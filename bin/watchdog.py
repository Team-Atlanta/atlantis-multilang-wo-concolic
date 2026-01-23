#!/usr/bin/env python3

import argparse
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from libCRS.otel import install_otel_logger


def setup_file_log_for_test(logfile: str) -> None:
    logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(logfile)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)


def log_testlang_status(workdir: str):
    testlang_dir = Path(workdir) / "harness-reverser"
    for testlang in testlang_dir.glob("testlang_*.out"):
        testlang_path = testlang.resolve()
        if testlang_path.exists():
            logging.info(f"[Harness Reverser] testlang created at {testlang_path}")
            return

    logging.info("[Harness Reverser] testlang is yet to be created")


def log_mlla_status(workdir: str):
    pattern = re.compile(r"mlla-result-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.yaml")
    matched_files = []

    for root, _, files in os.walk(workdir):
        for filename in files:
            match = pattern.match(filename)
            if match:
                timestamp_str = match.group(1)
                try:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                    full_path = os.path.join(root, filename)
                    matched_files.append((timestamp, full_path))
                except Exception:
                    continue

    matched_files.sort()

    logging.info(f"[MLLA] {len(matched_files)} results total:")
    for timestamp, path in matched_files:
        logging.info(
            f"[MLLA] Result at {timestamp.strftime('%Y-%m-%d %H:%M:%S')} found at: {path}"
        )


def log_corpus_status(corpus_dir: str):
    seeds = [
        f
        for f in os.listdir(corpus_dir)
        if os.path.isfile(os.path.join(corpus_dir, f)) and not f.startswith(".")
    ]
    num_seeds = len(seeds)
    logging.info(f"[Corpus] '{corpus_dir}' contains {num_seeds} seeds")


def log_coverage_status(cov_dir: str):
    seeds = [
        f
        for f in os.listdir(cov_dir)
        if os.path.isfile(os.path.join(cov_dir, f))
        and not f.startswith(".")
        and not f.endswith(".cov")
    ]

    missing_cov = []
    for seed in seeds:
        cov_file = os.path.join(cov_dir, seed + ".cov")
        if not os.path.isfile(cov_file):
            missing_cov.append(seed)

    logging.info(f"[Coverage] Total seeds + pov: {len(seeds)}")
    logging.info(f"[Coverage] Seeds or POVs missing .cov files: {len(missing_cov)}")
    if missing_cov:
        logging.info("[Coverage] List of seeds or POVs without .cov files:")
        for seed in missing_cov:
            logging.info(f"[Coverage]   - {seed}")


def log_pov_status(pov_dir: str):
    povs = [
        f
        for f in os.listdir(pov_dir)
        if os.path.isfile(os.path.join(pov_dir, f)) and not f.startswith(".")
    ]
    num_pov = len(povs)
    logging.info(f"[POV] '{pov_dir}' contains {num_pov} povs")


def log_uniafl_status(
    harness_name: str, workdir: str, corpus_dir: str, cov_dir: str, pov_dir: str
):
    logging.info("=" * 100)
    log_testlang_status(workdir)
    log_mlla_status(workdir)
    log_corpus_status(corpus_dir)
    log_coverage_status(cov_dir)
    log_pov_status(pov_dir)
    logging.info("=" * 100)


def cp_workdir_to_shared(harness_name: str, workdir: str):
    workdir = Path(workdir)
    shared = Path(os.getenv("SHARED_DIR", "/tmp/")) / harness_name
    for name in ["dictgen", "harness-reverser", "mlla"]:
        os.makedirs(str(shared / name), exist_ok=True)
        subprocess.run(
            ["rsync", "-a", f"{workdir / name}/.", str(shared / name)], check=False
        )


def copy_corpus_to_shared(harness_name: str, corpus_dir: str):
    shared_corpus = (
        Path(os.getenv("SHARED_DIR", "/tmp/")) / harness_name / "uniafl_corpus"
    )
    os.makedirs(str(shared_corpus), exist_ok=True)
    subprocess.run(["rsync", "-a", f"{corpus_dir}/.", str(shared_corpus)], check=False)


def main():
    parser = argparse.ArgumentParser(description="Watchdog script.")
    parser.add_argument(
        "--harness-name",
        dest="harness_name",
        required=True,
        help="Harness name",
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="Working directory path",
    )
    parser.add_argument(
        "--corpus-dir",
        dest="corpus_dir",
        required=True,
        help="Corpus directory path",
    )
    parser.add_argument(
        "--cov-dir",
        dest="cov_dir",
        required=True,
        help="Coverage directory path",
    )
    parser.add_argument(
        "--pov-dir",
        dest="pov_dir",
        required=True,
        help="Pov directory path",
    )
    parser.add_argument(
        "--interval",
        type=int,
        required=True,
        help="Logging interval in seconds",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    # setup_file_log_for_test(f"/{args.harness_name}.log")
    install_otel_logger(action_name="uniafl")

    while True:
        log_uniafl_status(
            args.harness_name, args.workdir, args.corpus_dir, args.cov_dir, args.pov_dir
        )
        if os.environ.get('TEST_ROUND', 'False') == 'True':
            cp_workdir_to_shared(args.harness_name, args.workdir)
            copy_corpus_to_shared(args.harness_name, args.corpus_dir)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
