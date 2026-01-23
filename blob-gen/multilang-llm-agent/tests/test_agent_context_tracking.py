import asyncio
from unittest.mock import Mock

from mlla.utils.agent_context import (
    clear_agent_context,
    get_agent_instance_context,
    get_agent_name_from_instance,
    set_agent_instance_context,
)
from mlla.utils.bedrock_callback import BedrockTokenUsageCallbackHandler, ModelUsage
from mlla.utils.llm import LLM
from tests.dummy_context import DummyContext


class TestAgentContextTracking:
    """Test suite for agent context tracking and metrics aggregation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.callback = BedrockTokenUsageCallbackHandler()
        clear_agent_context()

    def teardown_method(self):
        """Clean up after tests."""
        clear_agent_context()

    def test_agent_context_basic_operations(self):
        """Test basic agent context operations."""
        # Initially no context
        assert get_agent_instance_context() is None
        assert get_agent_name_from_instance() is None

        # Set context
        instance_id = "cpua_12345"
        set_agent_instance_context(instance_id)

        assert get_agent_instance_context() == instance_id
        assert get_agent_name_from_instance() == "cpua"

        # Clear context
        clear_agent_context()
        assert get_agent_instance_context() is None

    def test_agent_name_extraction(self):
        """Test agent name extraction from instance IDs."""
        test_cases = [
            ("cpua_12345", "cpua"),
            ("bcda_67890", "bcda"),
            ("orchestrator_11111", "orchestrator"),
            ("simple_name", "simple"),
            ("simple_name_1234", "simple_name"),
            ("nounderscore", "nounderscore"),  # Edge case - returns full string
        ]

        for instance_id, expected_name in test_cases:
            set_agent_instance_context(instance_id)
            assert get_agent_name_from_instance() == expected_name
            clear_agent_context()

    def test_llm_auto_detection_with_context(self):
        """Test LLM auto-detects agent name from context."""
        with DummyContext(no_llm=True) as dummy_gc:
            # Set agent context
            set_agent_instance_context("cpua_12345")

            # Create LLM without explicit agent_name
            llm = LLM(model="gpt-4", config=dummy_gc)

            # Should auto-detect agent name from context
            assert llm.agent_name == "cpua"
            assert llm.instance_id == "cpua_12345"

    def test_llm_explicit_agent_name_precedence(self):
        """Test explicit agent name takes precedence over context."""
        with DummyContext(no_llm=True) as dummy_gc:
            # Set agent context
            set_agent_instance_context("cpua_12345")

            # Create LLM with explicit agent_name
            llm = LLM(model="gpt-4", config=dummy_gc, agent_name="explicit_agent")

            # Should use explicit agent name
            assert llm.agent_name == "explicit_agent"
            assert llm.instance_id == "cpua_12345"  # Still gets context instance_id

    def test_llm_no_context_no_agent_name(self):
        """Test LLM behavior when no context and no explicit agent name."""
        with DummyContext(no_llm=True) as dummy_gc:
            # No context set
            clear_agent_context()

            # Create LLM without explicit agent_name
            llm = LLM(model="gpt-4", config=dummy_gc)

            # Should have empty agent name and None instance_id
            assert llm.agent_name == ""
            assert llm.instance_id is None

    def test_find_agent_instances(self):
        """Test finding agent instances from snapshots."""
        # Create some test snapshots
        self.callback.create_snapshot("cpua_12345_start")
        self.callback.create_snapshot("cpua_12345_end")
        self.callback.create_snapshot("cpua_67890_start")
        self.callback.create_snapshot("cpua_67890_end")
        self.callback.create_snapshot("bcda_11111_start")
        self.callback.create_snapshot("bcda_11111_end")
        self.callback.create_snapshot("other_snapshot")

        # Find CPUA instances
        cpua_instances = self.callback.find_agent_instances("cpua")
        assert set(cpua_instances) == {"cpua_12345", "cpua_67890"}

        # Find BCDA instances
        bcda_instances = self.callback.find_agent_instances("bcda")
        assert bcda_instances == ["bcda_11111"]

        # Find non-existent agent
        nonexistent_instances = self.callback.find_agent_instances("nonexistent")
        assert nonexistent_instances == []

    def test_snapshot_usage_tracking(self):
        """Test snapshot-based usage tracking for agent instances."""
        # Create mock usage data
        start_usage = ModelUsage(requests=0, total_tokens=0, cost=0.0)
        end_usage = ModelUsage(
            requests=2,
            total_tokens=1000,
            prompt_tokens=600,
            completion_tokens=400,
            cost=0.05,
        )

        # Manually create snapshots with usage data
        from mlla.utils.bedrock_callback import TokenUsageSnapshot

        start_snapshot = TokenUsageSnapshot(total_usage=start_usage)
        end_snapshot = TokenUsageSnapshot(total_usage=end_usage)

        self.callback.snapshots["cpua_12345_start"] = start_snapshot
        self.callback.snapshots["cpua_12345_end"] = end_snapshot

        # Get usage between snapshots
        usage_diff = self.callback.get_usage_between_snapshots(
            "cpua_12345_start", "cpua_12345_end"
        )

        assert usage_diff is not None
        assert usage_diff.total_usage.requests == 2
        assert usage_diff.total_usage.total_tokens == 1000
        assert usage_diff.total_usage.cost == 0.05

    def test_metrics_aggregation_logic(self):
        """Test the metrics aggregation logic used in display_agent_metrics."""
        from mlla.utils.display_metrics import display_agent_metrics

        # Use DummyContext with callback
        with DummyContext(no_llm=True) as dummy_gc:
            dummy_gc.general_callback = self.callback
            dummy_gc.get_execution_time = Mock(
                return_value=300.0
            )  # 5 minutes in seconds

            # Create multiple agent instances with usage data
            instances = ["cpua_12345", "cpua_67890"]

            for i, instance_id in enumerate(instances):
                start_usage = ModelUsage()
                end_usage = ModelUsage(
                    requests=1 + i,
                    total_tokens=500 * (i + 1),
                    prompt_tokens=300 * (i + 1),
                    completion_tokens=200 * (i + 1),
                    cost=0.025 * (i + 1),
                )

                from mlla.utils.bedrock_callback import TokenUsageSnapshot

                start_snapshot = TokenUsageSnapshot(total_usage=start_usage)
                end_snapshot = TokenUsageSnapshot(total_usage=end_usage)

                self.callback.snapshots[f"{instance_id}_start"] = start_snapshot
                self.callback.snapshots[f"{instance_id}_end"] = end_snapshot

            # Capture output
            output_lines = []

            def capture_output(text):
                output_lines.extend(text.split("\n"))

            # Test display_agent_metrics
            display_agent_metrics(
                dummy_gc, ["cpua"], capture_output, include_per_model=False
            )

            # Verify output contains aggregated metrics
            output_text = "\n".join(output_lines)
            assert "CPUA Agent:" in output_text
            assert "Number of Instances: 2" in output_text
            assert "Successful Requests: 3" in output_text  # 1 + 2
            assert "Total Tokens: 1500" in output_text  # 500 + 1000

    async def test_concurrent_agent_context_isolation(self):
        """Test that concurrent agents have isolated contexts."""
        results = {}

        async def agent_task(agent_name, instance_id):
            """Simulate an agent task with context."""
            set_agent_instance_context(instance_id)

            # Simulate some work
            await asyncio.sleep(0.01)

            # Record what this task sees
            results[instance_id] = {
                "context_instance": get_agent_instance_context(),
                "context_agent": get_agent_name_from_instance(),
            }

        # Run multiple concurrent agent tasks
        tasks = [
            agent_task("cpua", "cpua_12345"),
            agent_task("cpua", "cpua_67890"),
            agent_task("bcda", "bcda_11111"),
        ]

        await asyncio.gather(*tasks)

        # Verify each task saw its own context
        assert results["cpua_12345"]["context_instance"] == "cpua_12345"
        assert results["cpua_12345"]["context_agent"] == "cpua"

        assert results["cpua_67890"]["context_instance"] == "cpua_67890"
        assert results["cpua_67890"]["context_agent"] == "cpua"

        assert results["bcda_11111"]["context_instance"] == "bcda_11111"
        assert results["bcda_11111"]["context_agent"] == "bcda"

    async def test_nested_agent_context(self):
        """Test nested agent context handling with contextvars.

        This demonstrates that contextvars automatically handles nested contexts,
        where inner contexts can override outer contexts and are properly restored.
        """
        results = []

        async def outer_agent():
            """Outer agent that calls inner agent."""
            set_agent_instance_context("outer_agent_123")

            # Record outer context
            results.append(
                {
                    "phase": "outer_before",
                    "context": get_agent_instance_context(),
                    "agent": get_agent_name_from_instance(),
                }
            )

            # Call inner agent
            await inner_agent()

            # Record outer context after inner agent returns
            results.append(
                {
                    "phase": "outer_after",
                    "context": get_agent_instance_context(),
                    "agent": get_agent_name_from_instance(),
                }
            )

        async def inner_agent():
            """Inner agent that sets its own context."""
            set_agent_instance_context("inner_agent_456")

            # Record inner context
            results.append(
                {
                    "phase": "inner",
                    "context": get_agent_instance_context(),
                    "agent": get_agent_name_from_instance(),
                }
            )

            # Simulate some work
            await asyncio.sleep(0.01)

        # Run the nested agent scenario
        await outer_agent()

        # Verify context isolation and restoration
        assert len(results) == 3

        # Outer context before inner agent
        assert results[0]["phase"] == "outer_before"
        assert results[0]["context"] == "outer_agent_123"
        assert results[0]["agent"] == "outer_agent"

        # Inner context
        assert results[1]["phase"] == "inner"
        assert results[1]["context"] == "inner_agent_456"
        assert results[1]["agent"] == "inner_agent"

        # Outer context restored after inner agent
        assert results[2]["phase"] == "outer_after"
        assert results[2]["context"] == "outer_agent_123"
        assert results[2]["agent"] == "outer_agent"

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        # Test with None instance_id
        set_agent_instance_context(None)
        assert get_agent_instance_context() is None
        assert get_agent_name_from_instance() is None

        # Test with empty string
        set_agent_instance_context("")
        assert get_agent_instance_context() == ""
        assert get_agent_name_from_instance() == ""

        # Test find_agent_instances with empty snapshots
        empty_callback = BedrockTokenUsageCallbackHandler()
        assert empty_callback.find_agent_instances("any_agent") == []

        # Test get_usage_between_snapshots with non-existent snapshots
        usage = self.callback.get_usage_between_snapshots(
            "nonexistent_start", "nonexistent_end"
        )
        assert usage is None
