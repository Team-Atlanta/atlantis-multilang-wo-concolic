#!/usr/bin/env python3
"""Download GitHub repositories from CRS-Multilang project.yaml files"""

import argparse
from pathlib import Path
from typing import List

from crs_utils import (
    clone_repo,
    find_project_yamls,
    get_project_path,
    is_github_token_valid,
    load_config,
    setup_logger,
)
from loguru import logger


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Clone all benchmark repositories from CRS-Multilang"
    )
    parser.add_argument(
        "--crs-multilang-path", required=True, help="Path to CRS-Multilang repository"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "downloaded_repos"),
        help="Output directory for cloned repositories (default: ~/downloaded_repos)",
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for git clone operations (default: 300)",
    )

    return parser.parse_args()


def process_project_yamls(
    yaml_files: List[Path], base_path: str, output_dir: Path, timeout: int = 300
) -> None:
    """Process project.yaml files and clone repositories."""
    for yaml_path in yaml_files:
        logger.debug(f"Processing {yaml_path}")
        config = load_config(yaml_path)
        if not config:
            continue

        repo_url = config.get("main_repo")
        if not repo_url:
            logger.warning(f"No main_repo URL found in {yaml_path}")
            continue

        project_path = get_project_path(yaml_path, base_path)
        if project_path.parts and project_path.parts[0].startswith("example"):
            logger.debug(f"Skipping example project: {project_path}")
            continue

        clone_repo(repo_url, project_path, output_dir, timeout)


def main() -> None:
    """Main function to process projects and clone repositories."""
    args = parse_arguments()

    # Setup logger
    setup_logger(args.log_level)

    # Create output directory if it doesn't exist
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Using output directory: {output_dir}")

    # Check if GitHub token is valid before making any API calls
    if not is_github_token_valid():
        logger.error("Cannot clone repositories without a valid GitHub token. Exiting.")
        return

    # Find all project.yaml files
    yaml_files = find_project_yamls(args.crs_multilang_path)

    if not yaml_files:
        logger.error(
            "No project.yaml files found in"
            f" {args.crs_multilang_path}/benchmarks/projects"
        )
        return

    logger.success(f"Found {len(yaml_files)} project.yaml files")

    # Process yaml files and clone repositories
    process_project_yamls(yaml_files, args.crs_multilang_path, output_dir, args.timeout)


if __name__ == "__main__":
    main()
