#!/usr/bin/env python3

import os
import time
import subprocess
import logging
import argparse
from pathlib import Path
from libCRS.otel import install_otel_logger


def rsync_file(src, dst):
    return subprocess.run(["rsync", "-a", str(src), str(dst)], check=False)


def cp(src, dst):
    return subprocess.run(["cp", str(src), str(dst)], check=False)


class SeedShare:
    def __init__(
        self, workdir, harness_name, share_dir, our_src_dir, our_cov_dir, our_dst_dir
    ):
        self.workdir = Path(workdir)
        self.harness_name = harness_name
        self.share_dir = Path(share_dir)
        self.our_src_dir = Path(our_src_dir)
        self.our_cov_dir = Path(our_cov_dir)
        self.our_dst_dir = Path(our_dst_dir)
        self.crs_name = os.environ.get('CRS_NAME', 'crs-multilang')
        our_shared_dir = Path(share_dir) / self.crs_name
        os.makedirs(str(our_shared_dir), exist_ok=True)
        self.our_shared_dir = our_shared_dir

        self.loaded = set()
        self.stored = set()

    def info(self, msg):
        logging.info(f"[SeedShare][{self.harness_name}] {msg}")

    def sync(self):
        self.copy_ours_to_share()
        # Dynamically discover other CRS directories
        if self.share_dir.exists():
            for crs_dir in self.share_dir.iterdir():
                if crs_dir.is_dir() and crs_dir.name != self.crs_name:
                    self.copy_share_to_ours(crs_dir.name)

    def copy_ours_to_share(self):
        if not self.our_src_dir.exists():
            return
        n = 0
        for seed in self.our_src_dir.iterdir():
            if seed.name.startswith(".") or seed.name.endswith(".cov"):
                continue

            # Copy seed if not already stored
            if seed not in self.stored:
                self.stored.add(seed)
                dst = self.our_shared_dir / seed.name
                rsync_file(seed, dst)
                n += 1

            # Always check for coverage updates (outside stored check)
            cov_src = self.our_cov_dir / (seed.name + ".cov")
            cov_dst = self.our_shared_dir / ("." + seed.name + ".cov")
            if cov_src.exists() and not cov_dst.exists():
                rsync_file(cov_src, cov_dst)
        self.info(f"Share {self.our_src_dir} => {self.our_shared_dir}: {n}")

    def copy_share_to_ours(self, crs_name):
        src = self.share_dir / crs_name
        if not src.exists():
            return

        n = 0
        for src_seed in src.iterdir():
            # Skip hidden files and .cov files explicitly
            if src_seed in self.loaded or src_seed.name.startswith(".") or src_seed.name.endswith(".cov"):
                continue
            self.loaded.add(src_seed)
            # Copy seed
            workdir_dst = self.workdir / src_seed.name
            rsync_file(src_seed, workdir_dst)
            dst = self.our_dst_dir / src_seed.name
            cp(workdir_dst, dst)
            n += 1
            # Copy hidden coverage file .{seed_name}.cov if exists
            cov_src = src / ("." + src_seed.name + ".cov")
            if cov_src.exists():
                cov_dst = self.our_cov_dir / (src_seed.name + ".cov")
                rsync_file(cov_src, cov_dst)
        self.info(f"Share {src} => {self.our_dst_dir}: {n}")


def main():
    parser = argparse.ArgumentParser(description="seed sharing script")
    parser.add_argument(
        "--harness-name",
        dest="harness_name",
        required=True,
        help="Harness name",
    )
    parser.add_argument(
        "--share-dir",
        dest="share_dir",
        required=True,
        help="Shared Dir",
    )
    parser.add_argument(
        "--workdir",
        dest="workdir",
        required=True,
        help="work Dir",
    )
    parser.add_argument(
        "--our-src-dir",
        dest="our_src_dir",
        required=True,
        help="Our Corpus Source Dir",
    )
    parser.add_argument(
        "--our-cov-dir",
        dest="our_cov_dir",
        required=True,
        help="Our Coverage Source Dir",
    )
    parser.add_argument(
        "--our-dst-dir",
        dest="our_dst_dir",
        required=True,
        help="Our Corpus Dst Dir",
    )
    parser.add_argument(
        "--interval",
        type=int,
        required=True,
        help="Logging interval in seconds",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    install_otel_logger(action_name="uniafl")

    share = SeedShare(
        args.workdir,
        args.harness_name,
        args.share_dir,
        args.our_src_dir,
        args.our_cov_dir,
        args.our_dst_dir,
    )

    while True:
        time.sleep(args.interval)
        share.sync()


if __name__ == "__main__":
    main()
