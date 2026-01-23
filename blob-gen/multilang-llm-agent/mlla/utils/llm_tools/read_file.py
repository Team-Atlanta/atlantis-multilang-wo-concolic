from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from .. import instrument_line
from ..llm import PrioritizedTool


class ReadFileSchema(BaseModel):
    file_path: str


def create_read_file_tool() -> PrioritizedTool:
    def read_file_tool_function(file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                return instrument_line(content, start_number=1)[0]
        except FileNotFoundError:
            return f"[Error] File not found: {file_path}"
        except IsADirectoryError:
            return f"[Error] File is a directory: {file_path}"
        except Exception as e:
            return f"[Error]: {e}"

    tool = StructuredTool.from_function(
        name="read_file_tool",
        func=read_file_tool_function,
        args_schema=ReadFileSchema,
        description=(
            "Read a file from the file system. The file_path cannot be a directory."
        ),
    )

    return PrioritizedTool(
        1,
        tool,
    )
