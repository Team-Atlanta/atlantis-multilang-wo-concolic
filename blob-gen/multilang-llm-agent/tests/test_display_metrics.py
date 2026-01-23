from datetime import timedelta
from io import StringIO
from unittest.mock import Mock

import pytest

from mlla.utils.agent import BCDA, CPUA, ORCHESTRATOR_AGENT, SNAPSHOT_AGENTS
from mlla.utils.display_metrics import (
    aggregate_metrics,
    calculate_execution_time_from_instances,
    collect_snapshot_metrics,
    collect_snapshot_model_usage,
    display_agent_metrics,
)


class MockUsage:
    """Mock usage object that behaves like ModelUsage."""

    def __init__(
        self,
        requests=0,
        total_tokens=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost=0.0,
        cache_savings=0.0,
    ):
        self.requests = requests
        self.total_tokens = total_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        self.cache_savings = cache_savings

    def __add__(self, other):
        if other is None:
            return self
        return MockUsage(
            requests=self.requests + other.requests,
            total_tokens=self.total_tokens + other.total_tokens,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost=self.cost + other.cost,
            cache_savings=self.cache_savings + other.cache_savings,
        )


class MockSnapshot:
    """Mock snapshot object."""

    def __init__(self, total_usage, duration=None, model_usage=None):
        self.total_usage = total_usage
        self.duration = duration or timedelta(seconds=10)
        self.model_usage = model_usage or {}


@pytest.fixture
def mock_gc():
    """Create a mock GlobalContext with callback handler."""
    gc = Mock()
    gc.general_callback = Mock()
    gc.get_execution_time.return_value = 330.0  # 5 minutes 30 seconds in seconds

    gc.general_callback.total_usage = MockUsage(
        requests=10,
        total_tokens=1000,
        prompt_tokens=600,
        completion_tokens=400,
        cost=0.05,
        cache_savings=0.01,
    )

    gc.general_callback.model_usage = {
        "gpt-4": MockUsage(
            requests=5,
            total_tokens=500,
            prompt_tokens=300,
            completion_tokens=200,
            cost=0.03,
        ),
    }

    return gc


def test_snapshot_agents_constant():
    """Test SNAPSHOT_AGENTS contains expected agents."""
    assert CPUA in SNAPSHOT_AGENTS
    assert BCDA not in SNAPSHOT_AGENTS
    assert ORCHESTRATOR_AGENT not in SNAPSHOT_AGENTS


def test_calculate_execution_time_from_instances():
    """Test execution time calculation."""
    gc = Mock()

    # Test empty instances
    total_duration, avg_duration, instance_count = (
        calculate_execution_time_from_instances(gc, [])
    )
    assert total_duration == 0 and avg_duration is None and instance_count == 0

    # Test with data
    snapshot1 = Mock()
    snapshot1.duration = timedelta(seconds=10)
    snapshot2 = Mock()
    snapshot2.duration = timedelta(seconds=20)

    gc.general_callback.get_usage_between_snapshots.side_effect = [snapshot1, snapshot2]

    total_duration, avg_duration, instance_count = (
        calculate_execution_time_from_instances(gc, ["i1", "i2"])
    )
    assert total_duration == 30.0
    assert avg_duration == 15.0
    assert instance_count == 2


def test_aggregate_metrics():
    """Test metrics aggregation."""
    # Test None cases
    assert aggregate_metrics(None, None) is None

    metrics = {
        "requests": 5,
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost": 0.01,
        "cache_savings": 0.001,
    }
    assert aggregate_metrics(metrics, None) == metrics
    assert aggregate_metrics(None, metrics) == metrics

    # Test dict aggregation
    metrics1 = {
        "requests": 3,
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost": 0.01,
        "cache_savings": 0.001,
    }
    metrics2 = {
        "requests": 2,
        "total_tokens": 50,
        "prompt_tokens": 30,
        "completion_tokens": 20,
        "cost": 0.005,
        "cache_savings": 0.0005,
    }

    result = aggregate_metrics(metrics1, metrics2)
    assert result["requests"] == 5
    assert result["total_tokens"] == 150

    # Test object aggregation
    obj1 = MockUsage(requests=3, total_tokens=100)
    obj2 = MockUsage(requests=2, total_tokens=50)
    result = aggregate_metrics(obj1, obj2)
    assert result.requests == 5
    assert result.total_tokens == 150


def test_collect_snapshot_metrics():
    """Test snapshot metrics collection."""
    gc = Mock()

    # Test empty
    total_metrics, total_duration, instance_count = collect_snapshot_metrics(gc, [])
    assert total_metrics is None and total_duration == 0 and instance_count == 0

    # Test with data
    usage1 = {
        "requests": 2,
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost": 0.01,
        "cache_savings": 0.001,
    }
    usage2 = {
        "requests": 3,
        "total_tokens": 150,
        "prompt_tokens": 90,
        "completion_tokens": 60,
        "cost": 0.015,
        "cache_savings": 0.002,
    }

    snapshot1 = MockSnapshot(usage1, timedelta(seconds=10))
    snapshot2 = MockSnapshot(usage2, timedelta(seconds=15))

    gc.general_callback.get_usage_between_snapshots.side_effect = [snapshot1, snapshot2]

    total_metrics, total_duration, instance_count = collect_snapshot_metrics(
        gc, ["i1", "i2"]
    )
    assert total_metrics["requests"] == 5
    assert total_metrics["total_tokens"] == 250
    assert total_duration == 25
    assert instance_count == 2


def test_collect_snapshot_model_usage():
    """Test snapshot model usage collection."""
    gc = Mock()

    # Test empty
    assert collect_snapshot_model_usage(gc, []) == {}

    # Test with data
    model_usage1 = {"gpt-4": MockUsage(requests=2, total_tokens=100)}
    model_usage2 = {
        "gpt-4": MockUsage(requests=1, total_tokens=75),
        "claude": MockUsage(requests=1, total_tokens=25),
    }

    snapshot1 = MockSnapshot({}, model_usage=model_usage1)
    snapshot2 = MockSnapshot({}, model_usage=model_usage2)

    gc.general_callback.get_usage_between_snapshots.side_effect = [snapshot1, snapshot2]

    result = collect_snapshot_model_usage(gc, ["i1", "i2"])
    assert result["gpt-4"].requests == 3
    assert result["gpt-4"].total_tokens == 175
    assert result["claude"].requests == 1


def test_display_agent_metrics_basic(mock_gc):
    """Test basic display functionality."""
    output = StringIO()

    display_agent_metrics(mock_gc, [], output.write, include_per_model=False)
    result = output.getvalue()

    assert "=== Overall Usage Summary ===" in result
    assert "Execution Time: 330.00" in result
    assert "Successful Requests: 10" in result
    assert "Total Tokens: 1000" in result


def test_display_agent_metrics_per_model(mock_gc):
    """Test per-model usage display."""
    output = StringIO()

    display_agent_metrics(mock_gc, [], output.write, include_per_model=True)
    result = output.getvalue()

    assert "=== Per-Model Usage ===" in result
    assert "Model: gpt-4" in result


def test_display_agent_metrics_snapshot_agent(mock_gc):
    """Test snapshot agent display."""
    mock_gc.general_callback.find_agent_instances.return_value = ["cpua_1", "cpua_2"]

    usage1 = {
        "requests": 2,
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost": 0.01,
        "cache_savings": 0.001,
    }
    usage2 = {
        "requests": 3,
        "total_tokens": 150,
        "prompt_tokens": 90,
        "completion_tokens": 60,
        "cost": 0.015,
        "cache_savings": 0.002,
    }

    snapshot1 = MockSnapshot(usage1, timedelta(seconds=10))
    snapshot2 = MockSnapshot(usage2, timedelta(seconds=15))

    mock_gc.general_callback.get_usage_between_snapshots.side_effect = [
        snapshot1,
        snapshot2,
        snapshot1,
        snapshot2,
    ]

    output = StringIO()
    display_agent_metrics(mock_gc, [CPUA], output.write, include_per_model=False)
    result = output.getvalue()

    assert "CPUA Agent:" in result
    assert "Number of Instances: 2" in result
    assert "Total Execution Time: 25.00 secs" in result
    assert "Successful Requests: 5" in result


def test_display_agent_metrics_per_agent_usage(mock_gc):
    """Test per-agent usage display."""
    mock_gc.general_callback.get_agent_usage.return_value = {
        "requests": 3,
        "total_tokens": 150,
        "prompt_tokens": 90,
        "completion_tokens": 60,
        "cost": 0.015,
        "cache_savings": 0.002,
    }
    mock_gc.general_callback.find_agent_instances.return_value = ["test_1"]

    snapshot = Mock()
    snapshot.duration = timedelta(seconds=20)
    mock_gc.general_callback.get_usage_between_snapshots.return_value = snapshot

    output = StringIO()
    display_agent_metrics(
        mock_gc, ["test_agent"], output.write, include_per_model=False
    )
    result = output.getvalue()

    assert "TEST_AGENT Agent:" in result
    assert "Number of Instances: 1" in result
    assert "Successful Requests: 3" in result


def test_display_agent_metrics_edge_cases(mock_gc):
    """Test edge cases."""
    # No instances
    mock_gc.general_callback.find_agent_instances.return_value = []
    output = StringIO()
    display_agent_metrics(mock_gc, [CPUA], output.write)
    result = output.getvalue()
    assert "CPUA Agent" not in result

    # No agent usage
    mock_gc.general_callback.get_agent_usage.return_value = {}
    output = StringIO()
    display_agent_metrics(mock_gc, ["test_agent"], output.write)
    result = output.getvalue()
    assert "TEST_AGENT Agent" not in result
