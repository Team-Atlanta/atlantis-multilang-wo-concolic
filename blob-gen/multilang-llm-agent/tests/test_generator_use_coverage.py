"""Tests for generator agent coverage utilities."""

import pytest

from mlla.agents.generator_agent.graph import should_update_interesting_functions
from mlla.agents.generator_agent.state import GeneratorAgentOverallState
from mlla.agents.generator_agent.utils import merge_coverage


class TestMergeCoverage:
    """Test cases for merge_coverage function."""

    def test_empty_input(self):
        """Test merging empty coverage results."""
        assert merge_coverage([]) == {}

    def test_single_result(self):
        """Test merging single coverage result."""
        coverage_results = [
            {
                "coverage_info": {
                    "func1": {"src": "/file1.c", "lines": [1, 2, 3]},
                    "func2": {"src": "/file2.c", "lines": [10, 20]},
                }
            }
        ]

        result = merge_coverage(coverage_results)
        expected = {
            "func1": {"src": "/file1.c", "lines": [1, 2, 3]},
            "func2": {"src": "/file2.c", "lines": [10, 20]},
        }
        assert result == expected

    def test_line_merging_with_overlap(self):
        """Test core functionality: merging overlapping line coverage."""
        coverage_results = [
            {"coverage_info": {"func1": {"src": "/file1.c", "lines": [1, 2, 3]}}},
            {"coverage_info": {"func1": {"src": "/file1.c", "lines": [2, 3, 4, 5]}}},
        ]

        result = merge_coverage(coverage_results)
        expected = {"func1": {"src": "/file1.c", "lines": [1, 2, 3, 4, 5]}}
        assert result == expected

    def test_missing_data_handling(self):
        """Test handling of missing coverage_info and lines keys."""
        coverage_results = [
            {},  # Missing coverage_info
            {"coverage_info": {"func1": {"src": "/file1.c"}}},  # Missing lines
            {"coverage_info": {"func1": {"src": "/file1.c", "lines": [1, 2, 3]}}},
        ]

        result = merge_coverage(coverage_results)
        expected = {"func1": {"src": "/file1.c", "lines": [1, 2, 3]}}
        assert result == expected


class TestUpdateInterestingFunctionsConditional:
    """Test cases for update_interesting_functions conditional logic."""

    def test_standalone_mode_runs_update_interesting_functions(self, monkeypatch):
        """Test that update_interesting_functions runs only in standalone mode."""
        monkeypatch.setenv("BGA_GENERATOR_MAX_ITERATION", "2")

        state = GeneratorAgentOverallState(
            standalone=True,
            iter_cnt=1,
            payload={
                "coverage_results": [{"coverage_info": {"func1": {"lines": [1, 2]}}}],
                "merged_coverage": {"func1": {"lines": [1, 2]}},
            },
            crashed=False,
        )

        result = should_update_interesting_functions(state)
        assert result == "update_interesting_functions"

    @pytest.mark.skip(
        reason="This test is deprecated. non standalone mode now runs this"
    )
    def test_non_standalone_mode_skips_update_interesting_functions(self, monkeypatch):
        """Test that update_interesting_functions is skipped in non-standalone mode."""
        monkeypatch.setenv("BGA_GENERATOR_MAX_ITERATION", "2")

        state = GeneratorAgentOverallState(
            standalone=False,
            iter_cnt=1,
            payload={
                "coverage_results": [{"coverage_info": {"func1": {"lines": [1, 2]}}}],
                "merged_coverage": {"func1": {"lines": [1, 2]}},
            },
            crashed=False,
        )

        result = should_update_interesting_functions(state)
        assert result == "analyze_coverage"

    def test_crashed_state_goes_to_finalize(self):
        """Test that crashed state goes to finalize regardless of standalone mode."""
        state = GeneratorAgentOverallState(
            standalone=True,
            iter_cnt=1,
            payload={
                "coverage_results": [{"coverage_info": {"func1": {"lines": [1, 2]}}}],
                "merged_coverage": {"func1": {"lines": [1, 2]}},
            },
            crashed=True,
        )

        result = should_update_interesting_functions(state)
        assert result == "finalize"

    def test_no_merged_coverage_goes_to_finalize(self):
        """Test that missing merged_coverage goes to finalize."""
        state = GeneratorAgentOverallState(
            standalone=True,
            iter_cnt=1,
            payload={
                "coverage_results": [{"coverage_info": {"func1": {"lines": [1, 2]}}}]
            },
            crashed=False,
        )

        result = should_update_interesting_functions(state)
        assert result == "finalize"

    def test_max_iterations_reached_goes_to_finalize(self, monkeypatch):
        """Test that max iterations reached goes to finalize."""
        monkeypatch.setenv("BGA_GENERATOR_MAX_ITERATION", "2")

        state = GeneratorAgentOverallState(
            standalone=True,
            iter_cnt=2,
            payload={
                "coverage_results": [{"coverage_info": {"func1": {"lines": [1, 2]}}}],
                "merged_coverage": {"func1": {"lines": [1, 2]}},
            },
            crashed=False,
        )

        result = should_update_interesting_functions(state)
        assert result == "finalize"


if __name__ == "__main__":
    pytest.main([__file__])
