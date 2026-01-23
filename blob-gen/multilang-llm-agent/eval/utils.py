import sys
import textwrap
from pathlib import Path
from typing import List, Tuple

import yaml
from loguru import logger


def setup_logger():
    logger.remove()

    def safe_formatter(record):
        wrapped_message = textwrap.fill(
            record["message"], width=200, subsequent_indent="    "
        )
        exception = record["exception"] or ""

        wrapped_message = wrapped_message.replace("{", "{{").replace("}", "}}")
        wrapped_message = wrapped_message.replace("<", r"\<")

        # Define colors for different log levels
        reset_color = "\033[0m"
        level_colors = {
            "TRACE": "\033[36m",  # Cyan
            "DEBUG": "\033[34m",  # Blue
            "INFO": reset_color,
            "SUCCESS": "\033[32m",  # Green
            "WARNING": "\033[33m",  # Yellow
            "ERROR": "\033[31m",  # Red
            "CRITICAL": "\033[35m",  # Magenta
        }

        level = record["level"].name
        level_color = level_colors.get(level, "")

        return (
            "{time} | {level_color}{level:<8}{reset_color} |"
            " {name}:{function}:{line}\n↳"
            " {level_color}{message}{reset_color}\n{exception}\n".format(
                time=record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                level=level,
                level_color=level_color,
                reset_color=reset_color,
                name=record["name"],
                function=record["function"],
                line=record["line"],
                message=wrapped_message,
                exception=exception,
            )
        )

    logger.add(lambda msg: sys.stderr.write(msg), format=safe_formatter)


def get_latest_file(files: List[Path]) -> Path:
    """Get latest file sorted by timestamp in filename."""
    return sorted(files, key=lambda x: x.stem, reverse=True)[0]


def load_yaml_file(file_path: Path) -> dict:
    """Load and parse YAML file."""
    with open(file_path, "r") as f:
        return yaml.safe_load(f)


def find_all_model_dirs(base_path: str) -> List[Tuple[str | None, float | None, Path]]:
    """Find all model directories and extract model names and temperatures."""
    results: List[Tuple[str | None, float | None, Path]] = []
    base_dir = Path(base_path)

    if not base_dir.exists():
        logger.error(f"Base directory does not exist: {base_dir}")
        return results

    logger.info(f"Searching for base directories in: {base_dir}")

    # Look for directories matching pattern: base_model_t{temp}[_{number}]
    for path in base_dir.glob("*_*_t*"):
        if not str(path).startswith(str(base_path)):
            continue
        logger.info(f"Found path: {path}")
        try:
            # Split path name into components
            parts = path.name.split("_")

            # Find the temperature part (starts with 't')
            temp_part = next(p for p in parts if p.startswith("t"))
            if temp_part[1:]:  # No temperature value after 't'
                temp = float(temp_part[1:])
            else:
                temp = None

            # Extract temperature value
            temp_idx = parts.index(temp_part)
            if parts[temp_idx - 1]:
                model_str_lst = parts[temp_idx - 1 : temp_idx]
                model = "_".join(model_str_lst)
            else:
                model = None
            results.append((model, temp, path))
        except (ValueError, StopIteration):
            continue

    if not results:
        return [(None, None, Path(base_path))]

    return results
