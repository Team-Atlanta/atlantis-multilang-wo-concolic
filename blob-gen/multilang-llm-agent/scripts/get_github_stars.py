#!/usr/bin/env python3
"""GitHub Star Counter for CRS-Multilang Projects"""

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from crs_utils import (
    find_project_yamls,
    get_project_path,
    get_star_count,
    is_github_token_valid,
    load_config,
    load_existing_stars,
    save_stars,
    setup_logger,
)
from loguru import logger


def display_top_projects(
    configs: Dict[str, Dict[str, Any]],
    language_filter: Optional[List[str]] = None,
    limit: int = 30,
) -> None:
    """Display the top projects by star count."""
    data = list(configs.items())

    # Filter by language if specified
    if language_filter:
        logger.info(f"Filtering projects by languages: {', '.join(language_filter)}")
        data = [
            item
            for item in data
            if "language" in item[1] and item[1]["language"] in language_filter
        ]

    # Sort by star count
    data = sorted(data, key=lambda x: x[1].get("star", 0), reverse=True)

    # Display top projects
    logger.info(f"\nTop {min(limit, len(data))} projects by star count:")
    logger.info("-" * 80)
    for idx, (path, config) in enumerate(data[:limit], 1):
        language = config.get("language", "unknown")
        star = config.get("star", 0)
        repo = config.get("main_repo", "unknown")
        logger.info(f"[{idx}] {path}: {star} stars - {language} - {repo}")


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch GitHub star counts for CRS-Multilang benchmark repositories"
    )
    parser.add_argument(
        "--crs-multilang-path", required=True, help="Path to CRS-Multilang repository"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "downloaded_repos"),
        help="Output directory for results (default: ~/downloaded_repos)",
    )
    parser.add_argument(
        "--language-filter",
        nargs="+",
        help="Filter projects by language (e.g., 'jvm java')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Number of top projects to display (default: 30)",
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )
    parser.add_argument(
        "--crawl",
        action="store_true",
        help=(
            "Crawl GitHub to fetch star counts (if not specified, only use existing"
            " data)"
        ),
    )

    return parser.parse_args()


def process_project_yamls(
    yaml_files: List[Path],
    base_path: str,
    stars: Dict[str, int],
    output_dir: Path,
    crawl: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Process project.yaml files and optionally fetch star counts."""
    configs: Dict = {}

    # Check if GitHub token is valid before making any API calls
    if crawl and not is_github_token_valid():
        logger.error("Cannot crawl GitHub without a valid token.")
        return configs

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

        project_path_str = str(project_path)

        # First check local cache - always prioritize local data if available
        if project_path_str in stars and stars[project_path_str] is not None:
            config["star"] = stars[project_path_str]
            configs[project_path_str] = config
            logger.debug(
                f"Using cached star count for {project_path}: {stars[project_path_str]}"
            )
            continue

        # If not crawling, skip projects without existing star counts
        if not crawl:
            logger.debug(
                f"Skipping {project_path} (no cached star count and crawling disabled)"
            )
            continue

        # Only make API calls if crawling is enabled and we don't have cached data
        logger.debug(f"Fetching star count for {repo_url}")
        star = get_star_count(repo_url)
        if star is None:
            logger.debug(f"Could not get star count for {project_path}")
            continue

        stars[project_path_str] = star
        config["star"] = star
        configs[project_path_str] = config
        logger.success(f"Got star count for {project_path}: {star}")

        # Save intermediate results after each repository
        save_stars(stars, output_dir)

    return configs


def main() -> None:
    """Main function to process projects and fetch star counts."""
    args = parse_arguments()

    # Setup logger
    setup_logger(args.log_level)

    # Create output directory if it doesn't exist
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Using output directory: {output_dir}")

    # Load existing results
    stars = load_existing_stars(output_dir)
    logger.info(f"Loaded {len(stars)} existing star counts")

    # Find all project.yaml files
    yaml_files = find_project_yamls(args.crs_multilang_path)

    if not yaml_files:
        logger.error(
            "No project.yaml files found in"
            f" {args.crs_multilang_path}/benchmarks/projects"
        )
        return

    logger.success(f"Found {len(yaml_files)} project.yaml files")

    # Process yaml files and optionally fetch star counts
    if args.crawl:
        logger.info("Crawling GitHub to fetch star counts")
    else:
        logger.info("Using only existing star counts (use --crawl to fetch new data)")

    configs = process_project_yamls(
        yaml_files, args.crs_multilang_path, stars, output_dir, args.crawl
    )

    # Display top projects
    language_filter = args.language_filter if args.language_filter else None
    display_top_projects(configs, language_filter, args.limit)


if __name__ == "__main__":
    main()
