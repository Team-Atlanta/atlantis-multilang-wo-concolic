import argparse
from pathlib import Path
import logging
import os, sys
import json
from typing import Tuple
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.callbacks import get_openai_callback
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from .inconsistency import (
    parse_inconsistency,
    Inconsistency,
)
from .prompting import construct_user_prompt

# Define a new logging level for SUCCESS
SUCCESS = 25  # Between INFO (20) and WARNING (30)
logging.addLevelName(SUCCESS, "SUCCESS")


# Custom formatter with prompt-style prefixes
class CustomFormatter(logging.Formatter):
    def format(self, record):
        level_prefix = {
            logging.ERROR: "[-]",
            logging.WARNING: "[!]",
            logging.INFO: "[*]",
            logging.DEBUG: "[*]",
            SUCCESS: "[+]",
        }.get(record.levelno, "[*]")
        return f"{level_prefix} {super().format(record)}"


# Configure logging with custom formatter
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(CustomFormatter("%(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Model external functions based on symbolic propagation failures"
    )
    parser.add_argument(
        "--src", type=Path, default=Path("/src"), help="Path to source directory"
    )
    parser.add_argument(
        "--inconsistency",
        type=Path,
        required=True,
        help="Path to JSON file with coerced values indicating symbolic propagation failures",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-3-7-sonnet-20250219",
        help="Model to use for generating code",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to output file for generated code",
    )
    parser.add_argument(
        "--previous-code",
        type=Path,
        required=True,
        help="Path to previous code file for context",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("./workdir"),
        help="Path to working directory for intermediate files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the script without invoking the LLM",
    )
    args = parser.parse_args()
    return args


SYSTEM_PROMPT_PATH = Path(__file__).parent / "system-prompt.txt"


def handle_inconsistencies(
    inconsistency: Inconsistency,
    args: argparse.Namespace,
    url: str,
    api_key: str,
):
    previous_code = args.previous_code.read_text()
    user_prompt = construct_user_prompt(inconsistency, args.src, previous_code)
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    if not args.dry_run:
        llm = ChatOpenAI(base_url=url, api_key=SecretStr(api_key))
        with get_openai_callback() as cb:
            messages = llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ],
                model=args.model,
            )
        out = str(messages.content)
        with open(args.workdir / "cost.json", "w") as f:
            json.dump(
                {
                    "total_cost": cb.total_cost,
                    "total_tokens": cb.total_tokens,
                    "prompt_tokens": cb.prompt_tokens,
                    "reasoning_tokens": cb.reasoning_tokens,
                    "completion_tokens": cb.completion_tokens,
                },
                f,
            )

        if not (out.startswith("```python\n") and out.endswith("\n```")):
            raise ValueError(
                "Output does not start with '```python' and end with '```'. Please check the model output."
            )
        out = out[10:-4]  # Remove the ```python and ```

        with open(args.output, "w") as f:
            f.write(out)
    else:
        print("DRY RUN: LLM invocation skipped")
        print("Prompt: ")
        print(user_prompt)


def get_secrets() -> Tuple[str, str]:
    api_url = os.environ.get("LITELLM_URL")
    if not api_url:
        raise ValueError("LITELLM_URL environment variable not set")
    api_key = os.environ.get("LITELLM_KEY")
    if not api_key:
        raise ValueError("LITELLM_KEY environment variable not set")
    return api_url, api_key


def main():
    url, api_key = get_secrets()
    args = parse_args()
    # Validate paths
    if not args.src.exists() or not args.src.is_dir():
        raise ValueError(f"Source directory does not exist: {args.src}")

    if not args.inconsistency.exists() or not args.inconsistency.is_file():
        raise ValueError(
            f"Coerced values JSON file does not exist: {args.inconsistency}"
        )

    inconsistent_values = parse_inconsistency(args.inconsistency)
    handle_inconsistencies(inconsistent_values, args, url, api_key)


if __name__ == "__main__":
    main()
