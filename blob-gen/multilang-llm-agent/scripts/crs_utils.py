#!/usr/bin/env python3
"""Shared utilities for CRS-Multilang scripts"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml
from loguru import logger

# GitHub API configuration
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def normalize_git_url(repo_url: str) -> Optional[str]:
    """Convert various GitHub URL formats to owner/repo format."""
    if not repo_url:
        logger.error("Empty repository URL")
        return None

    # Handle git@ style URLs
    if repo_url.startswith("git@"):
        match = re.match(r"git@github\.com:([^/]+/[^.]+)(?:\.git)?", repo_url)
        if match:
            normalized = match.group(1)
            logger.debug(f"Normalized git URL: {repo_url} -> {normalized}")
            return normalized
    # Handle https:// style URLs
    elif repo_url.startswith("https://github.com/"):
        parts = repo_url.rstrip("/").split("/")
        normalized = f"{parts[-2]}/{parts[-1].replace('.git', '')}"
        logger.debug(f"Normalized https URL: {repo_url} -> {normalized}")
        return normalized
    # Handle raw owner/repo format
    elif "/" in repo_url and not any(c in repo_url for c in [":", "@", "http"]):
        normalized = repo_url.replace(".git", "")
        logger.debug(f"Already normalized format: {repo_url} -> {normalized}")
        return normalized

    logger.warning(f"Could not normalize URL format: {repo_url}")
    return None


def is_github_token_valid() -> bool:
    """Check if the GitHub token is valid."""
    if not GITHUB_TOKEN:
        logger.error(
            "GitHub token is not set. Set the GITHUB_TOKEN environment variable."
        )
        return False

    try:
        response = requests.get("https://api.github.com/user", headers=HEADERS)
        if response.status_code == 200:
            user_data = response.json()
            logger.info(
                f"GitHub token is valid. Authenticated as: {user_data.get('login')}"
            )
            return True
        elif response.status_code == 401:
            logger.error("GitHub token is invalid or expired.")
            return False
        else:
            logger.warning(
                f"Unexpected status code ({response.status_code}) when validating"
                " GitHub token."
            )
            return False
    except Exception as e:
        logger.error(f"Error validating GitHub token: {e}")
        return False


def check_rate_limit() -> bool:
    """Check GitHub API rate limit status and wait if necessary."""
    try:
        response = requests.get("https://api.github.com/rate_limit", headers=HEADERS)
        response.raise_for_status()
        data = response.json()

        core = data["resources"]["core"]
        remaining = core["remaining"]
        reset_time = datetime.fromtimestamp(core["reset"])

        if remaining == 0:
            wait_time = (reset_time - datetime.now()).total_seconds()
            if wait_time > 0:
                logger.warning(
                    f"Rate limit exceeded. Waiting {wait_time:.0f} seconds until"
                    f" {reset_time}..."
                )
                time.sleep(wait_time + 1)  # Add 1 second buffer
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking rate limit: {e}")
        return False


def get_star_count(repo_url: str) -> Optional[int]:
    """Get the star count for a GitHub repository."""
    # Check if repository exists and is accessible
    if not repo_exists(repo_url):
        logger.warning(
            f"Cannot get star count for non-existent or private repository: {repo_url}"
        )
        return None

    try:
        repo_path = normalize_git_url(repo_url)
        if not repo_path:
            logger.debug(f"Could not normalize repository URL: {repo_url}")
            return None

        # Check rate limit before making the API call
        check_rate_limit()

        api_url = f"https://api.github.com/repos/{repo_path}"
        response = requests.get(api_url, headers=HEADERS)

        # We already checked if the repo exists, so this should succeed
        data = response.json()
        return data.get("stargazers_count", 0)

    except Exception as e:
        logger.error(f"Error getting star count: {e}")
        return None


def find_project_yamls(crs_multilang_path: str) -> List[Path]:
    """Find all project.yaml files in the benchmarks/projects directory using BFS."""
    benchmark_path = Path(crs_multilang_path) / "benchmarks" / "projects"
    yaml_files = []

    # Use a queue for BFS traversal
    dirs_to_check = [benchmark_path]
    while dirs_to_check:
        current_dir = dirs_to_check.pop(0)

        try:
            # Check immediate children only
            found_project_yaml = False
            for item in current_dir.iterdir():
                if item.is_file() and item.name == "project.yaml":
                    yaml_files.append(item)
                    found_project_yaml = True
                    break  # Found project.yaml, don't add this directory's subdirs

            # Only add subdirectories if we didn't find a project.yaml
            if not found_project_yaml:
                for item in current_dir.iterdir():
                    if item.is_dir():
                        dirs_to_check.append(item)
        except PermissionError:
            logger.warning(f"Permission denied accessing {current_dir}")
            continue
        except Exception as e:
            logger.error(f"Error accessing {current_dir}: {e}")
            continue

    return yaml_files


def load_config(yaml_path: Path) -> Optional[Dict[str, Any]]:
    """Extract configuration from a project.yaml file."""
    try:
        with open(yaml_path, "r") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing {yaml_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading {yaml_path}: {e}")
        return None


def get_project_path(yaml_path: Path, base_path: str) -> Path:
    """Extract relative project path after 'projects' directory."""
    # Convert both paths to absolute paths for reliable comparison
    yaml_abs = yaml_path.absolute()
    base_abs = Path(base_path).absolute() / "benchmarks" / "projects"

    # Get the relative path after 'projects'
    try:
        return yaml_abs.parent.relative_to(base_abs)
    except ValueError:
        # Fallback to parent directory name if path structure is unexpected
        logger.warning(f"Unexpected path structure for {yaml_path}")
        return Path(yaml_path.parent.name)


def load_existing_stars(output_dir: Path) -> Dict[str, int]:
    """Load existing star counts from the output file."""
    star_fname = output_dir / "stars.json"
    if star_fname.exists():
        try:
            with star_fname.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("Could not load existing star counts, starting fresh")
    return {}


def save_stars(stars: Dict[str, int], output_dir: Path) -> None:
    """Save star counts to the output file."""
    star_fname = output_dir / "stars.json"
    with star_fname.open("w", encoding="utf-8") as f:
        json.dump(stars, f, indent=2)
    logger.info(f"Saved star counts to {star_fname}")


def repo_exists(repo_url: str) -> bool:
    """Check if a GitHub repository exists and is accessible."""
    repo_path = normalize_git_url(repo_url)
    if not repo_path:
        logger.warning(f"Could not normalize repository URL: {repo_url}")
        return False

    try:
        # Check rate limit before making the API call
        check_rate_limit()

        api_url = f"https://api.github.com/repos/{repo_path}"
        response = requests.get(api_url, headers=HEADERS)

        if response.status_code == 200:
            return True  # Repository exists and is accessible
        elif response.status_code == 404:
            logger.warning(f"Repository not found or private: {repo_url}")
            return False
        else:
            logger.warning(
                f"Unexpected status code ({response.status_code}) for {repo_url}"
            )
            return False
    except Exception as e:
        logger.error(f"Error checking if repository exists: {e}")
        return False


def clone_repo(
    repo_url: str, project_path: Path, output_dir: Path, timeout: int = 300
) -> None:
    """Clone a repository while preserving the directory structure."""
    # First check if the directory already exists
    output_path = output_dir / project_path
    if output_path.exists():
        logger.info(f"Directory {output_path} already exists, skipping...")
        return

    # Skip if not a GitHub URL
    if not (
        repo_url.startswith("https://github.com")
        or repo_url.startswith("git@github.com")
        or "github.com" in repo_url
    ):
        logger.warning(f"Skipping non-GitHub URL: {repo_url}")
        return

    # Check if repository exists and is accessible
    if not repo_exists(repo_url):
        logger.warning(f"Skipping non-existent or private repository: {repo_url}")
        return

    # Create parent directory if it doesn't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Normalize URL for better compatibility
        normalized_url = normalize_git_url(repo_url)
        if normalized_url:
            # Convert back to https URL format for cloning
            clone_url = f"https://github.com/{normalized_url}.git"
            logger.debug(f"Normalized URL for cloning: {repo_url} -> {clone_url}")
        else:
            # Use original URL if normalization fails
            clone_url = repo_url
            logger.debug(f"Using original URL for cloning: {clone_url}")

        logger.info(f"Cloning {clone_url} to {output_path} (timeout: {timeout}s)")
        import subprocess

        subprocess.run(
            ["git", "clone", clone_url, str(output_path)], check=True, timeout=timeout
        )
        logger.success(f"Successfully cloned {clone_url} to {output_path}")
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout ({timeout}s) expired while cloning {repo_url}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error cloning {repo_url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while cloning {repo_url}: {e}")


def setup_logger(log_level: str = "INFO") -> None:
    """Configure the logger with the specified log level."""
    import sys

    logger.remove()  # Remove default handler
    logger.add(
        sink=sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> |"
            " <level>{message}</level>"
        ),
    )
