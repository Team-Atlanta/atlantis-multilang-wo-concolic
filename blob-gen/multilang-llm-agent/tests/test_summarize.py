# flake8: noqa: E501
import json
import os
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
import tokencost
from langchain_community.agent_toolkits import FileManagementToolkit
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from mlla.utils import llm as llm_utils
from mlla.utils.llm import LLM, PrioritizedTool
from mlla.utils.llm_tools.astgrep import create_ag_tools
from mlla.utils.llm_tools.codeindexer import create_ci_tool
from mlla.utils.llm_tools.ripgrep import create_rg_tool

from .prompts import LONG_PROMPT, LONG_PROMPT_FOR_4O, PROMPT_45K, gen_near_limit_prompt
from .tools import search


@pytest.fixture(autouse=True)
def patch_large_context_model_name(monkeypatch):
    monkeypatch.setattr(
        llm_utils,
        "get_large_context_model_name",
        lambda model: ("gpt-4.1-nano", "gpt-4.1-mini"),
    )


@pytest.fixture(scope="session", autouse=True)
def setup_tokencost():
    try:
        json_path = (
            Path(os.path.dirname(__file__))
            / "../mlla/assets"
            / "model_prices_and_context_window.json"
        )
        if json_path.exists():
            data = json.load(open(json_path))
            tokencost.TOKEN_COSTS.update(data)
    except Exception as e:
        logger.warning(f"Error updating token costs: {e}")


def test_summarize_single_long_msg(setup_tokencost, config) -> None:
    """Test summarization with a single long message"""
    # Setup
    llm = LLM(
        model="gpt-4o-mini",
        config=config,
        temperature=0.6,
        prepare_large_context_model=False,
    )
    long_prompt = LONG_PROMPT_FOR_4O
    messages = [HumanMessage(content=long_prompt)]
    success = True

    # Invoke
    try:
        result = llm.invoke(messages)
        # Verify that we get a valid response and not an error message
        if result[-1].content == "LLM failed to generate a response.":
            success = False
    except Exception:
        # If it reaches here, the test failed (we expect no exception)
        success = False

    assert success


def test_summarize_system_and_long_msg(setup_tokencost, config) -> None:
    """Test summarization with a system message and a long message"""
    # Setup
    llm = LLM(
        model="gpt-4o-mini",
        config=config,
        temperature=0.6,
        prepare_large_context_model=False,
    )
    long_prompt = LONG_PROMPT_FOR_4O
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=long_prompt),
    ]
    success = True

    # Invoke
    try:
        result = llm.invoke(messages)
    except Exception:
        # If it reaches here, the test failed
        success = False
    logger.info(f"result: {result[-1]}")
    assert success


def test_summarize_with_multiple_message_parts(setup_tokencost, config) -> None:
    """Test summarization with single long message and multiple message parts"""
    # Setup
    llm = LLM(
        model="gpt-4o-mini",
        config=config,
        temperature=0.6,
        prepare_large_context_model=False,
    )
    # Use a smaller prompt that won't exceed the hard context limit
    # but will still trigger summarization
    long_prompt = gen_near_limit_prompt(100000)  # Reduced from 128000 to 100000
    messages = [HumanMessage(content=long_prompt)]
    success = True

    # Invoke - should succeed with summarization
    try:
        responses = llm.invoke(messages)
        if responses[-1].content == "LLM failed to generate a response.":
            success = False
    except Exception as e:
        logger.warning(f"Exception in first test: {e}")
        success = False

    assert success

    # Test with multiple message parts
    half_long_prompt = long_prompt[: len(long_prompt) // 2]
    another_half_long_prompt = long_prompt[len(long_prompt) // 2 :]
    # Reduced the multiplier to avoid context limit
    last_prompt = "This is the last prompt." * 1000  # Reduced from 1950 to 1000
    messages = [
        HumanMessage(content=half_long_prompt),
        HumanMessage(content=another_half_long_prompt),
        HumanMessage(content=last_prompt),
    ]
    success = True

    # Invoke - should succeed with summarization
    try:
        responses = llm.invoke(messages)
        if responses[-1].content == "LLM failed to generate a response.":
            logger.warning(f"last response: {responses[-1].content}")
            success = False
        else:
            logger.debug(f"last response: {responses[-1].content}")
    except Exception as e:
        logger.warning(f"Exception in second test: {e}")
        success = False

    assert success


def test_summarize_with_tool_call(setup_tokencost, config) -> None:
    """Test summarization with a tool call"""
    # Setup
    file_system_tools = FileManagementToolkit(
        root_dir=str(config.cp.cp_src_path),
        selected_tools=["read_file", "list_directory", "file_search"],
    ).get_tools()
    tools = []
    rg_tool = create_rg_tool(is_dev=False)
    if rg_tool:
        tools.append(rg_tool)
    tools.append(create_ci_tool(config))

    tools += create_ag_tools() + PrioritizedTool.from_tools(file_system_tools, 1)

    llm = LLM(
        model="gpt-4o-mini",
        config=config,
        temperature=0.6,
        tools=tools,
        prepare_large_context_model=False,
    )
    long_prompt = gen_near_limit_prompt(128000)
    messages = [HumanMessage(content=long_prompt)]
    success = True

    # Invoke
    try:
        responses = llm.invoke(messages)
    except Exception:
        # If it reaches here, the test passed
        success = False

    if responses[-1].content == "LLM failed to generate a response.":
        success = False
    else:
        logger.debug(f"last response: {responses[-1].content}")

    half_long_prompt = long_prompt[: len(long_prompt) // 2]
    another_half_long_prompt = long_prompt[len(long_prompt) // 2 :]
    last_prompt = "This is the last prompt." * 1925
    messages = [
        HumanMessage(content=half_long_prompt),
        HumanMessage(content=another_half_long_prompt),
        HumanMessage(content=last_prompt),
    ]
    success = True

    # Invoke
    try:
        responses = llm.invoke(messages)
    except Exception:
        # If it reaches here, the test passed
        success = False

    if responses[-1].content == "LLM failed to generate a response.":
        success = False
    else:
        logger.debug(f"last response: {responses[-1].content}")

    assert success


def test_summarize_with_multiple_msgs(setup_tokencost, config) -> None:
    """Test summarization with multiple messages"""
    # Setup
    llm = LLM(
        model="gpt-4o-mini",
        config=config,
        temperature=0.6,
        prepare_large_context_model=False,
    )
    long_prompt = LONG_PROMPT_FOR_4O

    messages = [HumanMessage(content=m) for m in long_prompt.split("\n")]

    # Invoke
    llm.invoke(messages)

    # Validate
    # logger.info(f"Response: {response}")


def test_summarize_with_merging_nodes(setup_tokencost, config, graph_config) -> None:
    """Test summarization with merging nodes"""
    memory = MemorySaver()
    tools = [search]
    tool_node = ToolNode(tools)

    # Setup
    def call_model(state: MessagesState):
        llm = LLM(
            model="gpt-4o-mini",
            config=config,
            temperature=0.6,
            prepare_large_context_model=False,
        )
        messages = state["messages"]
        logger.info(f"len of messages: {len(state['messages'])}")
        response = llm.invoke(messages)
        logger.info(f"len of response: {len(response)}")
        remove_messages = [m for m in response if isinstance(m, RemoveMessage)]
        logger.debug(f"len(remove_messages): {len(remove_messages)}")
        for idx, message in enumerate(response):
            if idx < 4:
                logger.debug(f"[{idx}] {message.type} ({len(message.content)})")
        return {"messages": response}

    # Define the function that determines whether to continue or not
    def should_continue(state: MessagesState) -> Literal["my_end", "action"]:
        messages = state["messages"]
        remove_messages = [m for m in messages if isinstance(m, RemoveMessage)]
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        logger.info(f"len of messages: {len(messages)}")
        logger.debug(f"len(remove_messages): {len(remove_messages)}")
        logger.debug(f"len(ai_messages): {len(ai_messages)}")
        for idx, message in enumerate(messages):
            if idx < 4:
                logger.debug(f"[{idx}] {message.type} ({len(message.content)})")
            if isinstance(message, AIMessage):
                logger.debug(f"[{idx}] {message.type} ({len(message.content)})")

        last_message = messages[-1]
        # If there is no tool call, then we finish
        if not last_message.tool_calls:
            return "my_end"
        # Otherwise if there is, we continue
        else:
            return "action"

    def my_start(state: MessagesState):
        long_prompt = LONG_PROMPT_FOR_4O
        messages = [HumanMessage(content=m) for m in long_prompt.split("\n")]
        state["messages"] = messages
        logger.info(f"len of messages: {len(state['messages'])}")
        return state

    def my_end(state: MessagesState):
        logger.info(f"len of messages: {len(state['messages'])}")
        return state

    workflow = StateGraph(MessagesState)
    workflow.add_node("my_start", my_start)
    workflow.add_node("agent", call_model)
    workflow.add_node("action", tool_node)
    workflow.add_node("my_end", my_end)

    workflow.add_edge(START, "my_start")
    workflow.add_edge("my_start", "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("action", "agent")
    workflow.add_edge("my_end", END)

    app = workflow.compile(checkpointer=memory)

    # image = app.get_graph(xray=2).draw_mermaid_png()
    # with open("test_graph.png", "wb") as f:
    #     f.write(image)

    # Invoke
    final_state = app.invoke({"messages": []}, graph_config)

    # Validate
    messages = final_state["messages"]
    assert len(messages) < 2048
    assert messages[-2].type != "ai"


def test_simple_graph(setup_tokencost) -> None:

    def node(state: MessagesState):
        messages = state["messages"]
        for idx, m in enumerate(messages):
            logger.info(f"[{idx}], {m.type} ({(m.content)})")
        state["messages"] = [HumanMessage(content="World!")]
        state["messages"].append(RemoveMessage(id=messages[0].id))
        return state

    workflow = StateGraph(MessagesState)
    workflow.add_node("node", node)

    workflow.add_edge(START, "node")
    workflow.add_edge("node", END)

    app = workflow.compile()

    # image = app.get_graph(xray=2).draw_mermaid_png()
    # with open("test_simple_graph.png", "wb") as f:
    #     f.write(image)

    final_state = app.invoke({"messages": [HumanMessage(content="Hello")]})
    messages = final_state["messages"]

    for idx, m in enumerate(messages):
        logger.info(f"[{idx}], {m.type} ({(m.content)})")


def test_summarize_behavior_with_and_without_large_context_model(
    setup_tokencost, config, request
) -> None:
    """
    Test summarization behavior with large context model available vs not available.
    When large_context_model is None: should trigger summarization
    When large_context_model is available: should skip summarization but may use large model
    """
    if request.config.getoption("--ci"):
        # Skip this test in CI because it is too expensive
        pytest.skip("Skipping test in CI mode because it uses LLM")
        return

    # Setup
    llm_check_context_limit = LLM(
        model="claude-sonnet-4-20250514",
        config=config,
        prepare_large_context_model=False,
    )
    llm = LLM(
        model="claude-sonnet-4-20250514",
        config=config,
    )
    # Use a prompt size that exceeds summarization threshold but may trigger context limit
    # PROMPT_45K * 4 = ~163K tokens - should trigger summarization when no large model
    long_prompt = PROMPT_45K * 4
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=long_prompt),
    ]

    dummy_exception = Exception("Dummy exception")

    # Test 1: Without large context model - should trigger summarization
    with patch.object(
        llm_check_context_limit,
        "_process_messages_for_token_limits",
        side_effect=dummy_exception,
    ) as summarize_mock:
        try:
            llm_check_context_limit.invoke(messages)
        except Exception as e:
            logger.info(f"the prompt invoke summarize: {e}")

        assert (
            summarize_mock.call_count == 1
        ), "Prompt is large enough to invoke summarization"

    # Test 2: With large context model - should skip summarization
    # but may still invoke large model if context limit is exceeded during actual invocation
    with patch.object(
        llm, "_process_messages_for_token_limits", side_effect=dummy_exception
    ) as summarize_mock, patch.object(
        llm.large_context_model, "_invoke", side_effect=dummy_exception
    ) as large_invoke_mock:
        # Invoke
        try:
            llm.invoke(messages)
        except Exception as e:
            logger.error(f"Error: {e}")

        assert summarize_mock.call_count == 0, "summarize_mock should not be called"
        # Note: large_invoke_mock may be called if the prompt exceeds the actual context limit
        # This is expected behavior - the large context model serves as a fallback
        logger.info(f"Large model invoked {large_invoke_mock.call_count} times")


def test_summarize_large_context_model(setup_tokencost, config) -> None:
    llm = LLM(
        model="claude-sonnet-4-20250514",
        config=config,
    )
    long_prompt = LONG_PROMPT
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=long_prompt),
    ]

    context_limit_error = Exception("ContextWindowExceededError")
    unresolvable_error = Exception(
        "generativelanguage.googleapis.com/generate_requests_per_model_per_day"
    )

    assert llm.large_context_model is not None
    assert llm.large_context_model_fallback is not None

    with patch.object(
        llm,
        "_process_messages_for_token_limits",
        wraps=llm._process_messages_for_token_limits,
    ) as original_summary, patch.object(
        llm.large_context_model,
        "_process_messages_for_token_limits",
        wraps=llm.large_context_model._process_messages_for_token_limits,
    ) as large_summary, patch.object(
        llm.large_context_model_fallback,
        "_process_messages_for_token_limits",
        wraps=llm.large_context_model_fallback._process_messages_for_token_limits,
    ) as large_fallback_summary, patch.object(
        llm, "_invoke", side_effect=context_limit_error
    ) as original_invoke, patch.object(
        llm.large_context_model, "_invoke", side_effect=unresolvable_error
    ) as large_invoke, patch.object(
        llm.large_context_model_fallback,
        "_invoke",
        wraps=llm.large_context_model_fallback._invoke,
    ) as large_fallback_invoke:
        # Invoke
        result = llm.invoke(messages)

        logger.info(f"result: {result[-1]}")

        assert original_summary.call_count == 0, "original_summary should not be called"
        assert large_summary.call_count == 1, "large_summary should not be called"
        assert (
            large_fallback_summary.call_count == 1
        ), "large_fallback_summary should not be called"
        assert (
            original_invoke.call_count == 1
        ), "original_invoke should be called, but context limit error is raised"
        assert (
            large_invoke.call_count == 1
        ), "large_invoke should be called, but context limit error is raised"
        assert (
            large_fallback_invoke.call_count == 1
        ), "large_fallback_invoke should be called, but context limit error is raised"
