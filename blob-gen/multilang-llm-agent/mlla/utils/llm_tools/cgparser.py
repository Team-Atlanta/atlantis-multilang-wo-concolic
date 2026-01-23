import asyncio
from typing import Optional

from langchain.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from ...agents.cgpa import CGParserAgent, CGParserInputState
from ..cg import FuncInfo
from ..context import GlobalContext
from ..llm import PrioritizedTool


class CGParserSchema(BaseModel):
    fn_name: str = Field(description="The name of the function to search for.")
    fn_file_path: Optional[str] = Field(
        description="The file path of the function to search for.", default=None
    )
    caller_file_path: Optional[str] = Field(
        description="The file path of the callsite of the function to search for.",
        default=None,
    )
    call_line: Optional[int] = Field(
        description="The line number of the function callsite in the caller file.",
        default=None,
    )


def create_cg_parser_tool(config: GlobalContext) -> PrioritizedTool:

    async def async_function_search_function(
        fn_name: str,
        fn_file_path: Optional[str] = None,
        caller_file_path: Optional[str] = None,
        call_line: Optional[int] = None,
    ) -> str:
        cg_parser_agent = CGParserAgent(config)
        graph = cg_parser_agent.compile()
        callsite_location = None
        if call_line:
            callsite_location = (call_line, None)

        logger.debug(
            f"Searching for function {fn_name} in {caller_file_path}@{call_line}"
        )

        res_state = await graph.ainvoke(
            CGParserInputState(
                fn_name=fn_name,
                fn_file_path=fn_file_path,
                caller_file_path=caller_file_path,
                caller_fn_body=None,
                callsite_location=callsite_location,
            )
        )

        fn_info: Optional[FuncInfo] = res_state.get("code_dict", None)

        if fn_info:
            return fn_info.pretty_str()
        elif caller_file_path:
            return f"No function call found in the {caller_file_path}"
        else:
            return "No function found"

    def sync_function_search_function(
        fn_name: str,
        fn_file_path: Optional[str] = None,
        caller_file_path: Optional[str] = None,
        call_line: Optional[int] = None,
    ) -> str:
        return asyncio.run(
            async_function_search_function(
                fn_name, fn_file_path, caller_file_path, call_line
            )
        )

    tool = StructuredTool.from_function(
        name="function_definition_by_name",
        func=sync_function_search_function,
        coroutine=async_function_search_function,
        args_schema=CGParserSchema,
        description=(
            "Get definition of a function by its name. Return function's information"
            " including its file path. If you know the file path of the function, you"
            " can provide it. If you know the caller's file path, which is calling the"
            " function, you can provide it. If you know the line number of the function"
            " call, you can provide it."
        ),
    )

    return PrioritizedTool(5, tool)
