from glob import glob
from pathlib import Path

from .gen import (
    generate_antlr4,
    generate_bmp,
    generate_gif,
    generate_jpeg,
    generate_png,
)

__all__ = [
    "generate_bmp",
    "generate_gif",
    "generate_jpeg",
    "generate_png",
    "generate_antlr4",
    "fetch_generators",
    "fetch_antlr4_generators",
]


def fetch_generators() -> list[str]:
    return sorted(["bmp", "gif", "jpeg", "png"] + fetch_antlr4_generators())


def fetch_antlr4_generators() -> list[str]:
    base_dir = Path(__file__).resolve().parent / "generated/antlr4"
    return list(
        map(
            lambda x: x[:-12],
            glob("**/*Generator.py", root_dir=base_dir, recursive=True),
        )
    )
