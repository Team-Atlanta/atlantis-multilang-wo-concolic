"""Test for message removal functionality in LangGraph workflow."""

# import os
from typing import Literal

import pytest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from mlla.utils.llm import LLM
from mlla.utils.telemetry import setup_telemetry
from tests.dummy_context import DummyContext
from tests.tools import search

# from langchain_openai import ChatOpenAI
# from openinference.instrumentation.langchain import LangChainInstrumentor
# from phoenix.otel import register

pytestmark = pytest.mark.skip(reason="This test is not for mlla.")


def setup():
    """Setup function to initialize environment and telemetry."""
    load_dotenv(".env.secret")
    project_name = "test_oi_removemessage"
    endpoint = "http://localhost:6006/v1/traces"

    setup_telemetry(
        project_name=project_name,
        endpoint=endpoint,
    )

    # # Initialize Phoenix tracer
    # tracer_provider = register(
    #     project_name=project_name,
    #     endpoint=endpoint,
    # )

    # # Initialize LangChain instrumentation
    # print("Setting up LangChain instrumentation")
    # instrumentor = LangChainInstrumentor()

    # # TODO: RemoveMessage support will be added when OpenInference supports it
    # # For now, these messages will be skipped in traces
    # instrumentor.instrument(tracer_provider=tracer_provider)

    # print(f"Phoenix telemetry enabled for project '{project_name}'")
    # print(
    #     "Note: RemoveMessage operations will be skipped in traces "
    #     "until OpenInference adds support"
    # )


def test_remove_manual():
    # Initialize components
    memory = MemorySaver()
    tools = [search]
    tool_node = ToolNode(tools)
    gc = DummyContext(False)
    model = LLM(model="gpt-4", config=gc)
    # model = ChatOpenAI(
    #     model_name="gpt-4o-mini",
    #     api_key=os.environ["OPENAI_TEST_KEY"]
    # )

    def call_model(state: MessagesState):
        response = model.invoke(state["messages"])
        return {"messages": response}

    def should_continue(state: MessagesState) -> Literal["action"]:
        """Return the next node to execute."""
        last_message = state["messages"][-1]
        # If there is no function call, then we finish
        if not last_message.tool_calls:
            return END
        # Otherwise if there is, we continue
        return "action"

    # Define workflow graph
    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", call_model)
    workflow.add_node("action", tool_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, ["action", END])
    workflow.add_edge("action", "agent")

    app = workflow.compile(checkpointer=memory)
    print("Workflow compiled successfully")

    config = {"configurable": {"thread_id": "2"}}
    input_message = HumanMessage(content="hi! I'm bob")
    for event in app.stream(
        {"messages": [input_message]}, config, stream_mode="values"
    ):
        event["messages"][-1].pretty_print()

    input_message = HumanMessage(content="what's my name?")
    for event in app.stream(
        {"messages": [input_message]}, config, stream_mode="values"
    ):
        event["messages"][-1].pretty_print()

    messages = app.get_state(config).values["messages"]
    print(messages)

    app.update_state(config, {"messages": RemoveMessage(id=messages[0].id)})
    messages = app.get_state(config).values["messages"]
    print(messages)


def test_remove_graph():
    # Initialize components
    memory = MemorySaver()
    tools = [search]
    tool_node = ToolNode(tools)
    gc = DummyContext(False)
    model = LLM(model="gpt-4", config=gc)
    # model = ChatOpenAI(
    #     model_name="gpt-4o-mini",
    #     api_key=os.environ["OPENAI_TEST_KEY"]
    # )

    def call_model(state: MessagesState):
        response = model.invoke(state["messages"])
        return {"messages": response}

    def delete_messages(state):
        messages = state["messages"]
        if len(messages) > 3:
            return {"messages": [RemoveMessage(id=m.id) for m in messages[:-3]]}
        return {"messages": []}

    def should_continue(state: MessagesState) -> Literal["action", "delete_messages"]:
        """Return the next node to execute."""
        last_message = state["messages"][-1]
        if not last_message.tool_calls:
            return "delete_messages"
        return "action"

    # Define workflow graph
    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", call_model)
    workflow.add_node("action", tool_node)
    workflow.add_node("delete_messages", delete_messages)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("action", "agent")
    workflow.add_edge("delete_messages", END)

    app = workflow.compile(checkpointer=memory)
    print("Workflow compiled successfully")
    print("Memory saver initialized:", isinstance(app.checkpointer, MemorySaver))

    config = {"configurable": {"thread_id": "3"}}
    input_message = HumanMessage(content="hi! I'm bob")
    for event in app.stream(
        {"messages": [input_message]}, config, stream_mode="values"
    ):
        print([(message.type, message.content) for message in event["messages"]])

    input_message = HumanMessage(content="what's my name?")
    for event in app.stream(
        {"messages": [input_message]}, config, stream_mode="values"
    ):
        print([(message.type, message.content) for message in event["messages"]])


def main():
    """Main function to test message removal functionality."""
    setup()
    test_remove_manual()
    test_remove_graph()


if __name__ == "__main__":
    main()
