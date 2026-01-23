#!/usr/bin/env python3

import os
import tempfile
from pathlib import Path

from mlla.utils.ci_parse import (
    _extract_agent_metrics,
    _extract_total_metrics,
    find_latest_result_file,
    parse_latest_result,
)

# Test content matching the new format from main.py
test_content = """
============== LLM Usage and Agent Metrics ====================

=== Overall Usage Summary ===
Total Usage:
  Execution Time: 663.56 secs
  Successful Requests: 95
  Total Tokens: 476514
  Input Tokens: 409788
  Output Tokens: 66726
  Total Cost: $1.2633
  Cache Savings: $0.4778

=== Agent Metrics ===
CPUA Agent:
  Number of Instances: 1
  Total Execution Time: 49.33 secs
  Average Execution Time: 49.33 secs
  Successful Requests: 9
  Total Tokens: 40974
  Input Tokens: 39276
  Output Tokens: 1698
  Total Cost: $0.0283
  Cache Savings: $0.0033
BCDA Agent:
  Number of Instances: 1
  Total Execution Time: 170.44 secs
  Average Execution Time: 170.44 secs
  Successful Requests: 42
  Total Tokens: 112565
  Input Tokens: 85501
  Output Tokens: 27064
  Total Cost: $0.2680
  Cache Savings: $0.0171
ORCHESTRATOR Agent:
  Number of Instances: 1
  Total Execution Time: 357.56 secs
  Average Execution Time: 357.56 secs
  Successful Requests: 44
  Total Tokens: 322975
  Input Tokens: 285011
  Output Tokens: 37964
  Total Cost: $0.9671
  Cache Savings: $0.4574
MUTATOR Agent:
  Number of Instances: 1
  Total Execution Time: 177.57 secs
  Average Execution Time: 177.57 secs
  Successful Requests: 25
  Total Tokens: 183141
  Input Tokens: 163689
  Output Tokens: 19452
  Total Cost: $0.4891
  Cache Savings: $0.2938
GENERATOR Agent:
  Number of Instances: 1
  Total Execution Time: 329.19 secs
  Average Execution Time: 329.19 secs
  Successful Requests: 12
  Total Tokens: 95802
  Input Tokens: 82780
  Output Tokens: 13022
  Total Cost: $0.3168
  Cache Savings: $0.1269
BLOBGEN Agent:
  Number of Instances: 3
  Total Execution Time: 751.76 secs
  Average Execution Time: 250.59 secs
  Successful Requests: 7
  Total Tokens: 44032
  Input Tokens: 38542
  Output Tokens: 5490
  Total Cost: $0.1612
  Cache Savings: $0.0368
==============================================================
"""


def test_total_metrics():
    print("Testing _extract_total_metrics...")
    try:
        total_metrics = _extract_total_metrics(test_content)
        print("✅ Total metrics extracted successfully:")
        print(f"  Execution time: {total_metrics.execution_time}")
        print(f"  Successful requests: {total_metrics.successful_requests}")
        print(f"  Total tokens: {total_metrics.tokens_used}")
        print(f"  Input tokens: {total_metrics.prompt_tokens}")
        print(f"  Output tokens: {total_metrics.completion_tokens}")
        print(f"  Total cost: ${total_metrics.total_cost}")

        # Verify specific expectations
        assert total_metrics.execution_time == 663.56
        assert total_metrics.successful_requests == 95
        assert total_metrics.tokens_used == 476514
        assert total_metrics.prompt_tokens == 409788
        assert total_metrics.completion_tokens == 66726
        assert total_metrics.total_cost == 1.2633
        assert total_metrics.cache_savings == 0.4778

        return True
    except Exception as e:
        print(f"❌ Error extracting total metrics: {e}")
        return False


def test_agent_metrics():
    print("\nTesting _extract_agent_metrics...")
    try:
        agent_metrics = _extract_agent_metrics(test_content)
        print("✅ Agent metrics extracted successfully:")
        for agent_name, metrics in agent_metrics.items():
            print(f"  {agent_name} Agent:")
            print(f"    Number of Instances: {metrics.number_of_instances}")
            print(f"    Total Execution Time: {metrics.total_execution_time}")
            print(f"    Average Execution Time: {metrics.average_execution_time}")
            print(f"    Successful requests: {metrics.successful_requests}")
            print(f"    Total tokens: {metrics.tokens_used}")
            print(f"    Input tokens: {metrics.prompt_tokens}")
            print(f"    Output tokens: {metrics.completion_tokens}")
            print(f"    Total cost: ${metrics.total_cost}")

        # Verify specific expectations for all 6 agents
        assert "CPUA" in agent_metrics
        assert agent_metrics["CPUA"].total_execution_time == 49.33
        assert agent_metrics["CPUA"].average_execution_time == 49.33
        assert agent_metrics["CPUA"].number_of_instances == 1
        assert agent_metrics["CPUA"].cache_savings == 0.0033

        assert "BCDA" in agent_metrics
        assert agent_metrics["BCDA"].total_execution_time == 170.44
        assert agent_metrics["BCDA"].average_execution_time == 170.44
        assert agent_metrics["BCDA"].number_of_instances == 1
        assert agent_metrics["BCDA"].cache_savings == 0.0171

        assert "ORCHESTRATOR" in agent_metrics
        assert agent_metrics["ORCHESTRATOR"].total_execution_time == 357.56
        assert agent_metrics["ORCHESTRATOR"].average_execution_time == 357.56
        assert agent_metrics["ORCHESTRATOR"].number_of_instances == 1
        assert agent_metrics["ORCHESTRATOR"].cache_savings == 0.4574

        assert "MUTATOR" in agent_metrics
        assert agent_metrics["MUTATOR"].total_execution_time == 177.57
        assert agent_metrics["MUTATOR"].average_execution_time == 177.57
        assert agent_metrics["MUTATOR"].number_of_instances == 1
        assert agent_metrics["MUTATOR"].cache_savings == 0.2938

        assert "GENERATOR" in agent_metrics
        assert agent_metrics["GENERATOR"].total_execution_time == 329.19
        assert agent_metrics["GENERATOR"].average_execution_time == 329.19
        assert agent_metrics["GENERATOR"].number_of_instances == 1
        assert agent_metrics["GENERATOR"].cache_savings == 0.1269

        assert "BLOBGEN" in agent_metrics
        assert agent_metrics["BLOBGEN"].total_execution_time == 751.76
        assert agent_metrics["BLOBGEN"].average_execution_time == 250.59
        assert agent_metrics["BLOBGEN"].number_of_instances == 3
        assert agent_metrics["BLOBGEN"].cache_savings == 0.0368

        print("✅ Agent metrics verification passed!")
        return True
    except Exception as e:
        print(f"❌ Error extracting agent metrics: {e}")
        return False


def test_standalone_mode():
    print("\nTesting standalone mode...")

    # Create a temporary directory structure for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create directory structure for both regular and standalone results
        regular_dir = temp_path / "aixcc/c/mock-cp-filein_harness"
        standalone_dir = temp_path / "aixcc/c/mock-cp-filein_harness-standalone"

        os.makedirs(regular_dir, exist_ok=True)
        os.makedirs(standalone_dir, exist_ok=True)

        # Create sample result files with timestamps
        regular_file = regular_dir / "mlla-result-2023-01-01_12-00-00.yaml"
        standalone_file = standalone_dir / "mlla-result-2023-01-02_12-00-00.yaml"

        # Write sample content to the files
        sample_content = """
blob_stats:
  succeeded: 1
  failed: 0
  total: 1
harness_status:
  filein_harness:
    exploited: true
    successful_blobs: 1
    total_blobs: 1
sanitizer_results: {}
"""
        with open(regular_file, "w") as f:
            f.write(sample_content)

        with open(standalone_file, "w") as f:
            f.write(sample_content)

        # Test finding the latest result file in regular mode
        try:
            result_file = find_latest_result_file(
                temp_path, "aixcc/c/mock-cp-filein_harness", is_standalone=False
            )
            print(f"✅ Regular mode found file: {result_file}")
            assert "standalone" not in str(
                result_file
            ), "Regular mode should not find standalone files"
        except Exception as e:
            print(f"❌ Error in regular mode test: {e}")
            return False

        # Test finding the latest result file in standalone mode
        try:
            result_file = find_latest_result_file(
                temp_path, "aixcc/c/mock-cp-filein_harness", is_standalone=True
            )
            print(f"✅ Standalone mode found file: {result_file}")
            assert "standalone" in str(
                result_file
            ), "Standalone mode should find standalone files"
        except Exception as e:
            print(f"❌ Error in standalone mode test: {e}")
            return False

        # Test parsing the latest result in standalone mode
        try:
            result = parse_latest_result(
                temp_path, "aixcc/c/mock-cp-filein_harness", is_standalone=True
            )
            print("✅ Successfully parsed standalone result")
            assert (
                result.blob_stats.succeeded == 1
            ), "Parsed result should have 1 succeeded blob"
            assert result.harness_status[
                "filein_harness"
            ].exploited, "Harness should be exploited"
        except Exception as e:
            print(f"❌ Error parsing standalone result: {e}")
            return False

        print("✅ All standalone mode tests passed!")
        return True
