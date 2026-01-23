#!/usr/bin/env python3

import tempfile
from pathlib import Path

import pytest

from bin.parse_ci_result import parse_stdout

pytestmark = pytest.mark.skip(reason="This file is for CI test.")


@pytest.fixture
def complete_stdout_content():
    """Fixture providing complete stdout content with all 6 agents."""
    return """
============== LLM Usage and Agent Metrics ====================

=== Overall Usage Summary ===
Total Usage:
  Execution Time: 1053.00 secs
  Successful Requests: 150
  Total Tokens: 1219647
  Input Tokens: 1145961
  Output Tokens: 73686
  Total Cost: $1.6646
  Cache Savings: $1.2413

=== Agent Metrics ===
CPUA Agent:
  Number of Instances: 1
  Total Execution Time: 325.07 secs
  Average Execution Time: 325.07 secs
  Successful Requests: 71
  Total Tokens: 801914
  Input Tokens: 788420
  Output Tokens: 13494
  Total Cost: $0.4944
  Cache Savings: $0.7442
BCDA Agent:
  Number of Instances: 1
  Total Execution Time: 88.30 secs
  Average Execution Time: 88.30 secs
  Successful Requests: 36
  Total Tokens: 93567
  Input Tokens: 71043
  Output Tokens: 22524
  Total Cost: $0.2268
  Cache Savings: $0.0161
ORCHESTRATOR Agent:
  Number of Instances: 1
  Total Execution Time: 332.47 secs
  Average Execution Time: 332.47 secs
  Successful Requests: 43
  Total Tokens: 324166
  Input Tokens: 286498
  Output Tokens: 37668
  Total Cost: $0.9435
  Cache Savings: $0.4811
MUTATOR Agent:
  Number of Instances: 1
  Total Execution Time: 230.84 secs
  Average Execution Time: 230.84 secs
  Successful Requests: 21
  Total Tokens: 137915
  Input Tokens: 121800
  Output Tokens: 16115
  Total Cost: $0.3880
  Cache Savings: $0.2191
GENERATOR Agent:
  Number of Instances: 1
  Total Execution Time: 332.29 secs
  Average Execution Time: 332.29 secs
  Successful Requests: 12
  Total Tokens: 114793
  Input Tokens: 100355
  Output Tokens: 14438
  Total Cost: $0.3339
  Cache Savings: $0.1838
BLOBGEN Agent:
  Number of Instances: 2
  Total Execution Time: 659.43 secs
  Average Execution Time: 329.72 secs
  Successful Requests: 10
  Total Tokens: 71458
  Input Tokens: 64343
  Output Tokens: 7115
  Total Cost: $0.2216
  Cache Savings: $0.0782
==============================================================
"""


@pytest.fixture
def incomplete_stdout_content():
    """Fixture providing incomplete stdout content missing some agents."""
    return """
============== LLM Usage and Agent Metrics ====================

=== Overall Usage Summary ===
Total Usage:
  Execution Time: 500.00 secs
  Successful Requests: 50
  Total Tokens: 100000
  Input Tokens: 80000
  Output Tokens: 20000
  Total Cost: $0.5000
  Cache Savings: $0.1000

=== Agent Metrics ===
CPUA Agent:
  Number of Instances: 1
  Total Execution Time: 100.00 secs
  Average Execution Time: 100.00 secs
  Successful Requests: 25
  Total Tokens: 50000
  Input Tokens: 40000
  Output Tokens: 10000
  Total Cost: $0.2500
  Cache Savings: $0.0500
BCDA Agent:
  Number of Instances: 1
  Total Execution Time: 200.00 secs
  Average Execution Time: 200.00 secs
  Successful Requests: 25
  Total Tokens: 50000
  Input Tokens: 40000
  Output Tokens: 10000
  Total Cost: $0.2500
  Cache Savings: $0.0500
==============================================================
"""


@pytest.fixture
def temp_stdout_file():
    """Fixture that creates and cleans up temporary stdout files."""
    temp_files = []

    def _create_temp_file(content):
        temp_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ci-stdout", delete=False
        )
        temp_file.write(content)
        temp_file.close()
        temp_file_path = Path(temp_file.name)
        temp_files.append(temp_file_path)
        return temp_file_path

    yield _create_temp_file

    # Cleanup
    for temp_file_path in temp_files:
        if temp_file_path.exists():
            temp_file_path.unlink()


def test_parse_stdout_complete_integration(complete_stdout_content, temp_stdout_file):
    """Test parse_stdout function with complete stdout content containing all agents."""
    # Create temporary file with complete content
    temp_file_path = temp_stdout_file(complete_stdout_content)

    # Parse the stdout
    result = parse_stdout(temp_file_path)

    # Verify total metrics
    total = result.total
    assert total.execution_time == 1053.00
    assert total.successful_requests == 150
    assert total.tokens_used == 1219647
    assert total.prompt_tokens == 1145961
    assert total.completion_tokens == 73686
    assert total.total_cost == 1.6646
    assert total.cache_savings == 1.2413

    # Verify all 6 agents are parsed
    agents = result.agents
    expected_agents = [
        "CPUA",
        "BCDA",
        "ORCHESTRATOR",
        "MUTATOR",
        "GENERATOR",
        "BLOBGEN",
    ]

    assert len(agents) == 6
    for agent_name in expected_agents:
        assert agent_name in agents, f"Agent {agent_name} not found in parsed results"


def test_parse_stdout_agent_metrics(complete_stdout_content, temp_stdout_file):
    """Test specific agent metrics parsing."""
    temp_file_path = temp_stdout_file(complete_stdout_content)
    result = parse_stdout(temp_file_path)
    agents = result.agents

    # Test CPUA agent
    assert agents["CPUA"].total_execution_time == 325.07
    assert agents["CPUA"].average_execution_time == 325.07
    assert agents["CPUA"].number_of_instances == 1
    assert agents["CPUA"].successful_requests == 71
    assert agents["CPUA"].tokens_used == 801914
    assert agents["CPUA"].total_cost == 0.4944
    assert agents["CPUA"].cache_savings == 0.7442

    # Test MUTATOR agent (the one that was missing in original issue)
    assert agents["MUTATOR"].total_execution_time == 230.84
    assert agents["MUTATOR"].average_execution_time == 230.84
    assert agents["MUTATOR"].number_of_instances == 1
    assert agents["MUTATOR"].successful_requests == 21
    assert agents["MUTATOR"].tokens_used == 137915
    assert agents["MUTATOR"].total_cost == 0.3880
    assert agents["MUTATOR"].cache_savings == 0.2191

    # Test BLOBGEN agent (multi-instance)
    assert agents["BLOBGEN"].total_execution_time == 659.43
    assert agents["BLOBGEN"].average_execution_time == 329.72
    assert agents["BLOBGEN"].number_of_instances == 2
    assert agents["BLOBGEN"].successful_requests == 10
    assert agents["BLOBGEN"].tokens_used == 71458
    assert agents["BLOBGEN"].total_cost == 0.2216
    assert agents["BLOBGEN"].cache_savings == 0.0782


def test_parse_stdout_incomplete_agents(incomplete_stdout_content, temp_stdout_file):
    """Test parse_stdout with content missing some agents."""
    temp_file_path = temp_stdout_file(incomplete_stdout_content)
    result = parse_stdout(temp_file_path)

    # Verify total metrics
    total = result.total
    assert total.execution_time == 500.00
    assert total.successful_requests == 50
    assert total.tokens_used == 100000
    assert total.total_cost == 0.5000

    # Verify only 2 agents are parsed
    agents = result.agents
    assert len(agents) == 2
    assert "CPUA" in agents
    assert "BCDA" in agents
    assert "MUTATOR" not in agents
    assert "ORCHESTRATOR" not in agents
    assert "GENERATOR" not in agents
    assert "BLOBGEN" not in agents


def test_parse_stdout_invalid_file():
    """Test parse_stdout with invalid file content."""
    invalid_content = """
This is not a valid stdout format.
No metrics here.
"""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ci-stdout", delete=False
    ) as temp_file:
        temp_file.write(invalid_content)
        temp_file_path = Path(temp_file.name)

    try:
        # Should raise ValueError for invalid content
        with pytest.raises(ValueError):
            parse_stdout(temp_file_path)
    finally:
        # File might be deleted by parse_stdout on error, so check if it exists
        if temp_file_path.exists():
            temp_file_path.unlink()


def test_parse_stdout_missing_total_metrics(temp_stdout_file):
    """Test parse_stdout with missing total metrics section."""
    content_without_total = """
============== LLM Usage and Agent Metrics ====================

=== Agent Metrics ===
CPUA Agent:
  Number of Instances: 1
  Total Execution Time: 100.00 secs
  Average Execution Time: 100.00 secs
  Successful Requests: 25
  Total Tokens: 50000
  Input Tokens: 40000
  Output Tokens: 10000
  Total Cost: $0.2500
  Cache Savings: $0.0500
==============================================================
"""

    temp_file_path = temp_stdout_file(content_without_total)

    # Should raise ValueError for missing total metrics
    with pytest.raises(ValueError):
        parse_stdout(temp_file_path)


def test_parse_stdout_missing_agent_metrics(temp_stdout_file):
    """Test parse_stdout with missing agent metrics section."""
    content_without_agents = """
============== LLM Usage and Agent Metrics ====================

=== Overall Usage Summary ===
Total Usage:
  Execution Time: 500.00 secs
  Successful Requests: 50
  Total Tokens: 100000
  Input Tokens: 80000
  Output Tokens: 20000
  Total Cost: $0.5000
  Cache Savings: $0.1000
==============================================================
"""

    temp_file_path = temp_stdout_file(content_without_agents)

    # Should raise ValueError for missing agent metrics
    with pytest.raises(ValueError):
        parse_stdout(temp_file_path)


def test_parse_stdout_negative_cache_savings(temp_stdout_file):
    """Test parse_stdout with negative Cache Savings values."""
    content_with_negative_cache = """
============== LLM Usage and Agent Metrics ====================

=== Overall Usage Summary ===
Total Usage:
  Execution Time: 141.20 secs
  Successful Requests: 32
  Total Tokens: 68342
  Input Tokens: 59370
  Output Tokens: 8972
  Total Cost: $0.1559
  Cache Savings: $-0.0468

=== Agent Metrics ===
BLOBGEN Agent:
  Number of Instances: 1
  Total Execution Time: 41.93 secs
  Average Execution Time: 41.93 secs
  Successful Requests: 1
  Total Tokens: 3149
  Input Tokens: 2328
  Output Tokens: 821
  Total Cost: $0.0210
  Cache Savings: $-0.0017
CPUA Agent:
  Number of Instances: 1
  Total Execution Time: 32.92 secs
  Average Execution Time: 32.92 secs
  Successful Requests: 18
  Total Tokens: 38287
  Input Tokens: 37461
  Output Tokens: 826
  Total Cost: $0.0324
  Cache Savings: $0.0442
==============================================================
"""

    temp_file_path = temp_stdout_file(content_with_negative_cache)
    result = parse_stdout(temp_file_path)

    # Verify total metrics with negative cache savings
    total = result.total
    assert total.execution_time == 141.20
    assert total.successful_requests == 32
    assert total.tokens_used == 68342
    assert total.total_cost == 0.1559
    assert total.cache_savings == -0.0468

    # Verify agents are parsed correctly despite negative cache savings
    agents = result.agents
    assert len(agents) == 2
    assert "BLOBGEN" in agents
    assert "CPUA" in agents

    # Verify BLOBGEN agent with negative cache savings
    assert agents["BLOBGEN"].total_execution_time == 41.93
    assert agents["BLOBGEN"].average_execution_time == 41.93
    assert agents["BLOBGEN"].number_of_instances == 1
    assert agents["BLOBGEN"].successful_requests == 1
    assert agents["BLOBGEN"].tokens_used == 3149
    assert agents["BLOBGEN"].total_cost == 0.0210
    assert agents["BLOBGEN"].cache_savings == -0.0017

    # Verify CPUA agent with positive cache savings
    assert agents["CPUA"].total_execution_time == 32.92
    assert agents["CPUA"].average_execution_time == 32.92
    assert agents["CPUA"].number_of_instances == 1
    assert agents["CPUA"].successful_requests == 18
    assert agents["CPUA"].tokens_used == 38287
    assert agents["CPUA"].total_cost == 0.0324
    assert agents["CPUA"].cache_savings == 0.0442
