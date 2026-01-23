import asyncio
from typing import List

from langchain.tools import StructuredTool
from pydantic import BaseModel
from tokencost import count_string_tokens

from ...codeindexer.parser import CIFunctionRes
from ..context import GlobalContext
from ..llm import PrioritizedTool


class CITool:
    gc: GlobalContext

    def __init__(self, config: GlobalContext):
        self.gc = config

    async def search_function(self, fn_name: str) -> List[CIFunctionRes]:
        return await self.gc.code_indexer.search_function(fn_name)


class CIFunctionSchema(BaseModel):
    fn_name: str


def create_ci_tool(config: GlobalContext) -> PrioritizedTool:

    async def async_function_search_function(fn_name) -> List[str]:
        if "." in fn_name:
            fn_name = fn_name.split(".")[-1]
        result = await CITool(config).search_function(fn_name)
        result_str = str(result)
        token_cnt = count_string_tokens(result_str, "gpt-4o")
        if token_cnt > 120000:
            raise Exception(
                "The results are too long. Use other tools to search for the function."
            )
        return [r.pretty_str() for r in result]

    def sync_function_search_function(fn_name) -> List[str]:
        return asyncio.run(async_function_search_function(fn_name))

    tool = StructuredTool.from_function(
        name="function_search_tool",
        func=sync_function_search_function,
        coroutine=async_function_search_function,
        args_schema=CIFunctionSchema,
        description=(
            "Search for a specific function by function name in the code index. If"
            " there are multiple functions with the same name, all of them will be"
            " returned."
        ),
    )

    return PrioritizedTool(5, tool)


# TODO: tool for directly executing tree-sitter-query
