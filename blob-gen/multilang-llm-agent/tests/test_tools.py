from functools import partial
from pathlib import Path
from typing import List

import pytest
from langchain_community.agent_toolkits import FileManagementToolkit
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from mlla.agents.cpua import CPUnderstandAgent, CPUnderstandOverallState
from mlla.main import preprocess
from mlla.prompts.cpua import (
    GET_FILE_PATH_FORMAT,
    GET_FILE_PATH_HUMAN,
    GET_FILE_PATH_SYSTEM,
)
from mlla.utils.cp import init_cp_repo, sCP, sCP_Harness
from mlla.utils.llm import LLM, PrioritizedTool
from mlla.utils.llm_tools.astgrep import AGTool, create_ag_tools
from mlla.utils.llm_tools.codeindexer import create_ci_tool
from mlla.utils.llm_tools.ripgrep import RGTool, create_rg_tool
from mlla.utils.telemetry import setup_telemetry


class TestState(CPUnderstandOverallState):
    cp_path: Path


@pytest.mark.skip(reason="This test is not yet implemented.")
def test_java(cp_jenkins_path, config) -> None:
    memory = MemorySaver()

    file_tools = FileManagementToolkit(
        selected_tools=["list_directory", "file_search"]
    ).get_tools()

    tools = []
    rg_tool = create_rg_tool(is_dev=False)
    if rg_tool:
        tools.append(rg_tool)
    tools.append(create_ci_tool(config))

    tools += create_ag_tools() + PrioritizedTool.from_tools(file_tools, 1)

    setup_telemetry(
        project_name="test_tools for jenkins",
    )

    def mock_get_file_path(state: TestState) -> TestState:
        harnesses: dict[str, sCP_Harness] = {}
        for _, harness in config.cp.harnesses.items():
            if not harness.src_path.exists():
                continue
            harnesses[harness.name] = harness

        llm = LLM(model="gpt-4o", config=config, temperature=0.6, tools=tools)
        extension_list = state["extension_list"]

        filtered_files: List[Path] = []
        for file in config.cp.list_files_recursive():
            if file.suffix in extension_list:
                filtered_files.append(file)

        api_dict = state["api_dict"]

        # Build harness information
        harness_info = []
        for _, harness in harnesses.items():
            if not harness.src_path.exists():
                continue
            with harness.src_path.open("r") as f:
                code = f.read()
            harness_info.append(
                GET_FILE_PATH_FORMAT.format(
                    name=harness.name,
                    path=harness.src_path,
                    api=api_dict[harness.name],
                    code=code,
                )
            )
        messages = [
            HumanMessage(
                GET_FILE_PATH_SYSTEM.format(
                    project_dir=config.cp.cp_src_path.as_posix()
                )
                + GET_FILE_PATH_HUMAN.format(harness_info="\n\n".join(harness_info))
            ),
        ]
        response = llm.invoke(messages)
        state["messages"] = response
        return state

    def need_tool(state: TestState) -> bool:
        messages = state["messages"]
        if len(messages) == 0:
            return False
        last_message = messages[-1]
        if last_message.tool_calls:
            logger.debug(f"Tool calls: {last_message.additional_kwargs['tool_calls']}")
            return True
        return False

    def before_end(state: TestState) -> TestState:
        last_message = state["messages"][-1]
        logger.info(f"last_message: {last_message}")
        return state

    def gen_simple_cpua() -> CompiledStateGraph:
        builder = StateGraph(TestState)
        cpua = CPUnderstandAgent(config)
        _tools = [tool.get_tool() for tool in tools]
        grep_tool_node = ToolNode(_tools)
        # builder.add_node("filter_files_by_lang", cpua.filter_files_by_lang)
        builder.add_node("preprocess", partial(preprocess, config))
        builder.add_node("cpua_preprocess", cpua.preprocess)
        builder.add_node("understand_harnesses", cpua.understand_harnesses)
        builder.add_node("mock_get_file_path", mock_get_file_path)
        builder.add_node(
            "call_model_with_grep_tools",
            partial(cpua.call_model_with_tools, tools=tools),
        )
        builder.add_node("grep_tools", grep_tool_node)
        builder.add_node("before_end", before_end)

        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "cpua_preprocess")
        builder.add_edge("cpua_preprocess", "understand_harnesses")
        builder.add_edge("understand_harnesses", "mock_get_file_path")
        builder.add_conditional_edges(
            "mock_get_file_path", need_tool, {True: "grep_tools", False: "before_end"}
        )
        builder.add_edge("grep_tools", "call_model_with_grep_tools")
        builder.add_conditional_edges(
            "call_model_with_grep_tools",
            need_tool,
            {True: "grep_tools", False: "before_end"},
        )
        builder.add_edge("before_end", END)

        graph = builder.compile(memory)

        return graph

    graph = gen_simple_cpua()

    final_state = graph.invoke(
        {
            "cp_path": cp_jenkins_path,
        },
        config.graph_config,
    )

    api_dict = final_state["api_dict"]
    cp: sCP = config.cp

    logger.info(f"result: {api_dict}")
    harnesses = {
        cp.harnesses[harness_id].name: cp.harnesses[harness_id].src_path
        for harness_id in cp.harnesses
    }

    logger.info(f"harnesses: {harnesses}")

    # async with lsp.start_server():
    #     for harness_name, api_list in api_dict.items():
    #         filepath = harnesses[harness_name].relative_to(args.source_dir).as_posix()
    #         for _api, line, col in api_list:
    #             logger.info(f"filepath, line, col: {filepath}, {line}, {col}")
    #             result = await lsp.request_definition(filepath, int(line), int(col))
    #             logger.info(f"definition: {result}")


def test_search_function_definition_Java(cp_jenkins_path) -> None:
    init_cp_repo(cp_jenkins_path)
    util_main = cp_jenkins_path / (
        "./repo/plugins/pipeline-util-plugin/src/main/java/io/jenkins/plugins/"
        + "UtilPlug/UtilMain.java"
    )
    ag_tool = AGTool()
    results = ag_tool.search_function_definition(
        "doexecCommandUtils", util_main.as_posix()
    )

    for r in results:
        logger.info(f"result: {r}")


def test_search_type_definition_Java(cp_jenkins_path) -> None:
    init_cp_repo(cp_jenkins_path)
    util_main = cp_jenkins_path / (
        "./repo/plugins/pipeline-util-plugin/src/main/java/io/jenkins/plugins/"
        + "UtilPlug/UtilMain.java"
    )
    ag_tool = AGTool()
    results = ag_tool.search_type_definition("UtilMain", util_main.as_posix())

    for r in results:
        logger.info(f"result: {r}")


def test_ngx_get_conf_rg_tool(cp_nginx_path) -> None:
    # Initialize the CP repository first
    init_cp_repo(cp_nginx_path)

    rg_tool = RGTool()
    file_path = cp_nginx_path / "repo"
    result = rg_tool.search_in("ngx_get_conf", file_path.as_posix())
    assert result != "No results found."
