import asyncio
import json
from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import pytest
import tokencost
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.graph import MessagesState, add_messages

from mlla.utils.agent import BaseAgentTemplate
from mlla.utils.bedrock_callback import BedrockTokenUsageCallbackHandler
from mlla.utils.state import merge_with_update

from .dummy_context import DummyContext


class MockInputState(MessagesState):
    query: str


class MockOutputState(MessagesState):
    result: Annotated[str, merge_with_update]


class MockOverallState(MockInputState, MockOutputState):
    step: Annotated[int, merge_with_update]


class MockAgent(BaseAgentTemplate):
    def __init__(self, gc, name, enable_usage_snapshot=False):
        ret_dir = gc.RESULT_DIR / name
        super().__init__(
            gc,
            ret_dir,
            MockInputState,
            MockOutputState,
            MockOverallState,
            enable_usage_snapshot=enable_usage_snapshot,
        )
        self.name = name
        self.builder.add_node("process_and_generate", self.process_and_generate)
        self.builder.add_edge("preprocess", "process_and_generate")
        self.builder.add_edge("process_and_generate", "finalize")
        self.mock_llm = Mock()
        self.mock_llm.invoke = self.mock_invoke

    def deserialize(self, state, content):
        pass

    def serialize(self, state):
        pass

    def preprocess(self, state):
        pass

    def finalize(self, state):
        pass

    def process_and_generate(self, state):
        query = state["query"]
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content=f"Answer this question: {query}"),
        ]
        response = self.mock_llm.invoke(messages)
        return MockOutputState(
            messages=add_messages(messages, [response]),
            result=f"Answer to '{query}' from {self.name}",
        )

    def mock_invoke(self, messages, cache_read=0, cache_creation=0):
        input_token_details = {
            "cache_read": cache_read,
            "cache_creation": cache_creation,
        }

        run_id = f"run_{id(messages)}_{self.name}"

        self.gc.general_callback.on_llm_start(
            serialized={"kwargs": {"model": "gpt-4.1-nano"}},
            prompts=[msg.content for msg in messages if hasattr(msg, "content")],
            run_id=run_id,
            agent_name=self.module_name,
        )

        response = AIMessage(content=f"Response from {self.name}")
        response.response_metadata = {"model_name": "gpt-4.1-nano"}
        response.usage_metadata = {
            "total_tokens": 100,
            "input_tokens": 50,
            "output_tokens": 50,
            "input_token_details": input_token_details,
        }

        mock_llm_result = LLMResult(
            generations=[[ChatGeneration(message=response)]],
            llm_output={
                "token_usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 50,
                    "total_tokens": 100,
                    "input_token_details": input_token_details,
                },
                "model_name": "gpt-4.1-nano",
            },
        )

        self.gc.general_callback.on_llm_end(response=mock_llm_result, run_id=run_id)
        return response


@pytest.fixture
def global_context():
    json_path = Path("mlla") / "assets" / "model_prices_and_context_window.json"
    if json_path.exists():
        data = json.load(open(json_path))
        tokencost.TOKEN_COSTS.update(data)

    context = DummyContext()
    context.general_callback = BedrockTokenUsageCallbackHandler()
    return context


def _verify_agent_usage(agent_usage, expected_calls=1):
    assert agent_usage is not None
    assert agent_usage["total_tokens"] == expected_calls * 100
    assert agent_usage["prompt_tokens"] == expected_calls * 50
    assert agent_usage["completion_tokens"] == expected_calls * 50
    assert agent_usage["requests"] == expected_calls


def _verify_global_usage(callback, expected_total_calls):
    assert callback.total_usage.total_tokens == expected_total_calls * 100
    assert callback.total_usage.prompt_tokens == expected_total_calls * 50
    assert callback.total_usage.completion_tokens == expected_total_calls * 50
    assert callback.total_usage.requests == expected_total_calls


def _calculate_cache_savings(cache_read=0, cache_creation=0):
    from mlla.utils.bedrock_callback import (
        calculate_cache_savings,
        calculate_token_cost,
    )

    token_usage = {
        "input_tokens": 50,
        "output_tokens": 50,
        "input_token_details": {
            "cache_read": cache_read,
            "cache_creation": cache_creation,
        },
    }

    actual_cost = calculate_token_cost(token_usage, "gpt-4.1-nano")

    return calculate_cache_savings(
        prompt_tokens=50,
        completion_tokens=50,
        model_id="gpt-4.1-nano",
        actual_cost=actual_cost,
        token_usage=token_usage,
    )


async def _run_agents_concurrently(global_context, num_agents, queries_per_agent=1):
    agents = [MockAgent(global_context, f"agent_{i}") for i in range(num_agents)]
    graphs = [agent.compile() for agent in agents]

    tasks = []
    for i, graph in enumerate(graphs):
        for j in range(queries_per_agent):
            tasks.append(asyncio.to_thread(graph.invoke, {"query": f"Query {i}_{j}"}))

    await asyncio.gather(*tasks)
    return agents


def test_single_agent_basic_usage(global_context):
    agent = MockAgent(global_context, "test_agent")
    graph = agent.compile()
    graph.invoke({"query": "What is the capital of France?"})

    agent_usage = global_context.general_callback.get_agent_usage(agent.module_name)
    _verify_agent_usage(agent_usage)
    _verify_global_usage(global_context.general_callback, 1)


def test_multiple_agents_usage_isolation(global_context):
    agent1 = MockAgent(global_context, "agent_1")
    agent2 = MockAgent(global_context, "agent_2")

    graph1 = agent1.compile()
    graph2 = agent2.compile()

    graph1.invoke({"query": "Query 1"})
    graph2.invoke({"query": "Query 2"})
    graph2.invoke({"query": "Query 3"})

    agent1_usage = global_context.general_callback.get_agent_usage(agent1.module_name)
    agent2_usage = global_context.general_callback.get_agent_usage(agent2.module_name)

    _verify_agent_usage(agent1_usage, 1)
    _verify_agent_usage(agent2_usage, 2)
    _verify_global_usage(global_context.general_callback, 3)


def test_per_model_usage_tracking(global_context):
    agent1 = MockAgent(global_context, "agent_1")
    agent2 = MockAgent(global_context, "agent_2")

    graph1 = agent1.compile()
    graph2 = agent2.compile()

    graph1.invoke({"query": "Query 1"})
    graph2.invoke({"query": "Query 2"})
    graph2.invoke({"query": "Query 3"})

    agent1_usage = global_context.general_callback.get_agent_usage(agent1.module_name)
    agent2_usage = global_context.general_callback.get_agent_usage(agent2.module_name)

    assert "gpt-4.1-nano" in agent1_usage["model_usage"]
    assert agent1_usage["model_usage"]["gpt-4.1-nano"]["total_tokens"] == 100
    assert agent1_usage["model_usage"]["gpt-4.1-nano"]["requests"] == 1

    assert "gpt-4.1-nano" in agent2_usage["model_usage"]
    assert agent2_usage["model_usage"]["gpt-4.1-nano"]["total_tokens"] == 200
    assert agent2_usage["model_usage"]["gpt-4.1-nano"]["requests"] == 2

    model_usage = global_context.general_callback.get_model_usage("gpt-4.1-nano")
    assert model_usage["gpt-4.1-nano"].total_tokens == 300
    assert model_usage["gpt-4.1-nano"].requests == 3


def test_cache_read_savings(global_context):
    agent = MockAgent(global_context, "cache_agent")
    agent.mock_llm.invoke = lambda messages: agent.mock_invoke(messages, cache_read=30)

    graph = agent.compile()
    graph.invoke({"query": "Cached query"})

    agent_usage = global_context.general_callback.get_agent_usage(agent.module_name)
    expected_savings = _calculate_cache_savings(cache_read=30)

    assert agent_usage["cache_savings"] == pytest.approx(expected_savings, abs=1e-6)
    assert global_context.general_callback.total_usage.cache_savings == pytest.approx(
        expected_savings, abs=1e-6
    )


def test_cache_creation_savings(global_context):
    agent = MockAgent(global_context, "cache_agent")
    agent.mock_llm.invoke = lambda messages: agent.mock_invoke(
        messages, cache_creation=25
    )

    graph = agent.compile()
    graph.invoke({"query": "Cache creation query"})

    agent_usage = global_context.general_callback.get_agent_usage(agent.module_name)
    expected_savings = _calculate_cache_savings(cache_creation=25)

    assert agent_usage["cache_savings"] == pytest.approx(expected_savings, abs=1e-6)
    assert global_context.general_callback.total_usage.cache_savings == pytest.approx(
        expected_savings, abs=1e-6
    )


def test_mixed_cache_savings(global_context):
    agent = MockAgent(global_context, "cache_agent")
    agent.mock_llm.invoke = lambda messages: agent.mock_invoke(
        messages, cache_read=20, cache_creation=15
    )

    graph = agent.compile()
    graph.invoke({"query": "Mixed cache query"})

    agent_usage = global_context.general_callback.get_agent_usage(agent.module_name)
    expected_savings = _calculate_cache_savings(cache_read=20, cache_creation=15)

    assert agent_usage["cache_savings"] == pytest.approx(expected_savings, abs=1e-6)
    assert global_context.general_callback.total_usage.cache_savings == pytest.approx(
        expected_savings, abs=1e-6
    )


def test_multiple_agents_cache_aggregation(global_context):
    agent1 = MockAgent(global_context, "agent_1")
    agent2 = MockAgent(global_context, "agent_2")

    agent1.mock_llm.invoke = lambda messages: agent1.mock_invoke(
        messages, cache_read=30
    )
    agent2.mock_llm.invoke = lambda messages: agent2.mock_invoke(
        messages, cache_read=10, cache_creation=20
    )

    graph1 = agent1.compile()
    graph2 = agent2.compile()

    graph1.invoke({"query": "Agent 1 query"})
    graph2.invoke({"query": "Agent 2 query"})

    agent1_usage = global_context.general_callback.get_agent_usage(agent1.module_name)
    agent2_usage = global_context.general_callback.get_agent_usage(agent2.module_name)

    expected_savings1 = _calculate_cache_savings(cache_read=30)
    expected_savings2 = _calculate_cache_savings(cache_read=10, cache_creation=20)

    assert agent1_usage["cache_savings"] == pytest.approx(expected_savings1, abs=1e-6)
    assert agent2_usage["cache_savings"] == pytest.approx(expected_savings2, abs=1e-6)

    expected_total_savings = expected_savings1 + expected_savings2
    assert global_context.general_callback.total_usage.cache_savings == pytest.approx(
        expected_total_savings, abs=1e-6
    )


def test_snapshots_functionality(global_context):
    agent = MockAgent(global_context, "snapshot_agent")
    agent.mock_llm.invoke = lambda messages: agent.mock_invoke(
        messages, cache_read=25, cache_creation=15
    )

    graph = agent.compile()

    # Manually create snapshots since agents no longer create them automatically
    global_context.general_callback.create_snapshot(f"{agent.module_name}_start")
    graph.invoke({"query": "Snapshot test query"})
    global_context.general_callback.create_snapshot(f"{agent.module_name}_end")

    usage = global_context.general_callback.get_usage_between_snapshots(
        f"{agent.module_name}_start", f"{agent.module_name}_end"
    )

    assert usage is not None
    assert usage.total_usage.total_tokens == 100
    assert usage.total_usage.prompt_tokens == 50
    assert usage.total_usage.completion_tokens == 50
    assert usage.total_usage.requests == 1

    expected_savings = _calculate_cache_savings(cache_read=25, cache_creation=15)
    assert usage.total_usage.cache_savings == pytest.approx(expected_savings, abs=1e-6)


def test_get_all_agent_usage(global_context):
    agent1 = MockAgent(global_context, "agent_1")
    agent2 = MockAgent(global_context, "agent_2")

    agent1.mock_llm.invoke = lambda messages: agent1.mock_invoke(
        messages, cache_read=20
    )
    agent2.mock_llm.invoke = lambda messages: agent2.mock_invoke(
        messages, cache_creation=15
    )

    graph1 = agent1.compile()
    graph2 = agent2.compile()

    graph1.invoke({"query": "Agent 1 query"})
    graph2.invoke({"query": "Agent 2 query 1"})
    graph2.invoke({"query": "Agent 2 query 2"})

    all_usage = global_context.general_callback.get_all_agent_usage()

    assert agent1.module_name in all_usage
    assert agent2.module_name in all_usage
    assert all_usage[agent1.module_name]["total_tokens"] == 100
    assert all_usage[agent2.module_name]["total_tokens"] == 200
    assert all_usage[agent1.module_name]["cache_savings"] > 0
    assert all_usage[agent2.module_name]["cache_savings"] > 0


def test_concurrent_agents_race_conditions(global_context):
    num_agents = 5
    queries_per_agent = 10

    agents = asyncio.run(
        _run_agents_concurrently(global_context, num_agents, queries_per_agent)
    )

    for agent in agents:
        agent_usage = global_context.general_callback.get_agent_usage(agent.module_name)
        _verify_agent_usage(agent_usage, queries_per_agent)

    expected_total_calls = num_agents * queries_per_agent
    _verify_global_usage(global_context.general_callback, expected_total_calls)


def test_empty_agent_usage_query(global_context):
    # Test querying usage for non-existent agent
    usage = global_context.general_callback.get_agent_usage("non_existent_agent")
    assert usage == {}


def test_model_usage_query_specific_model(global_context):
    agent = MockAgent(global_context, "test_agent")
    graph = agent.compile()
    graph.invoke({"query": "Test query"})

    # Test getting usage for specific model
    model_usage = global_context.general_callback.get_model_usage("gpt-4.1-nano")
    assert "gpt-4.1-nano" in model_usage
    assert model_usage["gpt-4.1-nano"].total_tokens == 100

    # Test getting usage for non-existent model
    empty_usage = global_context.general_callback.get_model_usage("non-existent-model")
    assert "non-existent-model" in empty_usage
    assert empty_usage["non-existent-model"].total_tokens == 0


def test_snapshot_creation_and_retrieval(global_context):
    agent = MockAgent(global_context, "snapshot_agent")
    graph = agent.compile()

    # Create initial snapshot
    initial_snapshot = global_context.general_callback.create_snapshot("initial")
    assert initial_snapshot.total_usage.total_tokens == 0

    # Run some operations
    graph.invoke({"query": "Query 1"})
    graph.invoke({"query": "Query 2"})

    # Create final snapshot
    final_snapshot = global_context.general_callback.create_snapshot("final")
    assert final_snapshot.total_usage.total_tokens == 200

    # Test snapshot retrieval
    retrieved_initial = global_context.general_callback.get_snapshot("initial")
    retrieved_final = global_context.general_callback.get_snapshot("final")

    assert retrieved_initial is not None
    assert retrieved_final is not None
    assert retrieved_initial.total_usage.total_tokens == 0
    assert retrieved_final.total_usage.total_tokens == 200

    # Test non-existent snapshot
    non_existent = global_context.general_callback.get_snapshot("non_existent")
    assert non_existent is None


def test_snapshot_diff_calculation(global_context):
    agent = MockAgent(global_context, "diff_agent")
    graph = agent.compile()

    # Create start snapshot
    global_context.general_callback.create_snapshot("start")

    # Run operations
    graph.invoke({"query": "Query 1"})
    graph.invoke({"query": "Query 2"})

    # Create end snapshot
    global_context.general_callback.create_snapshot("end")

    # Test diff calculation
    diff = global_context.general_callback.get_usage_between_snapshots("start", "end")

    assert diff is not None
    assert diff.total_usage.total_tokens == 200
    assert diff.total_usage.requests == 2

    # Test diff with non-existent snapshots
    invalid_diff = global_context.general_callback.get_usage_between_snapshots(
        "invalid1", "invalid2"
    )
    assert invalid_diff is None
