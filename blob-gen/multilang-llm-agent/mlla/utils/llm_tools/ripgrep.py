import os
import shutil
import subprocess
from typing import Optional

from langchain.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel
from tokencost import count_string_tokens

from ..llm import PrioritizedTool


def is_ripgrep_installed() -> bool:
    """Check if ripgrep (rg) is installed."""
    return shutil.which("rg") is not None


class RGTool:

    def search_in(self, query: str, path: str) -> str:
        """
        Search for a specific string in all files within the base directory.
        :param query: The string to search for.
        :return: A dictionary where keys are file paths and values are lists of
        matching lines.
        """

        if not os.path.exists(path):
            raise Exception(f"The path ({path}) does not exist.")

        try:
            # Limit search to specific file types and increase timeout
            cmds = [
                "timeout",
                "1m",
                "rg",
                "-C",
                "1",
                "-n",
                "--color=never",
                "-t",
                "c",
                "-t",
                "cpp",
                "-t",
                "h",
                "-t",
                "java",
                query,
                path,
            ]
            result = subprocess.run(
                cmds,
                text=True,
                capture_output=True,
                check=True,
                shell=False,
            )

        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                return "No results found."
            elif e.returncode == 2:
                logger.debug(f"Error occurred while searching for {query}: {e}")
                logger.debug(f"cmd: {' '.join(cmds)}")
                raise Exception(
                    "An error occurred while searching for the query. The error:"
                    f" {e}\n stderr: {e.stderr}"
                )
            else:
                logger.error(f"Error occurred while searching for {query}: {e}")
                logger.error(f"cmd: {' '.join(cmds)}")
                raise Exception(
                    "An error occurred while searching for the query. The error:"
                    f" {e}\n stderr: {e.stderr}"
                )
        except FileNotFoundError:
            logger.error("The ripgrep command is not available on this system.")
            return ""
        except Exception as e:
            logger.error(f"Error occurred while searching for {query}: {e}")
            return ""

        return result.stdout


class RGSchema(BaseModel):
    query: str
    path: str


def create_rg_tool(is_dev: bool) -> Optional[PrioritizedTool]:
    """
    Create a LangChain-compatible Tool for the ripgrep functionality.
    :param base_directory: The base directory to search in.
    :return: A LangChain Tool for ripgrep.
    """

    if not is_ripgrep_installed():
        if is_dev:
            raise RuntimeError("ripgrep (rg) is not installed on this system.")
        else:
            logger.error("ripgrep (rg) is not installed on this system.")
            return None

    grep_tool = RGTool()

    def rip_grep_tool_function(query: str, path: str) -> str:
        if not query:
            return "You must provide a query."
        if not path:
            path = "/src/repo"

        results = grep_tool.search_in(query, path)
        results_str = str(results)
        token_cnt = count_string_tokens(results_str, "gpt-4o")

        if token_cnt > 120000:
            raise Exception(
                "The results are too long. Please provide a more specific query."
            )

        return results_str

    tool = StructuredTool(
        name="ripgrep_tool",
        func=rip_grep_tool_function,
        args_schema=RGSchema,
        description=(
            "Search tool that recursively searches the path for regex patterns,"
            " using ripgrep."
            "This tool must require two arguments: query and path."
            "If you don't provide a path, the default path (/src/repo) will be used."
        ),
    )

    return PrioritizedTool(_tool=tool, priority=1)
