#! /usr/bin/env python3

import argparse
import json
import os
import pprint
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from dataclasses_json import DataClassJsonMixin, dataclass_json

from mlla.utils.ci_parse import (
    AgentMetrics,
    BlobInfo,
    CIResult,
    HarnessStatus,
    TotalMetrics,
    _extract_agent_metrics,
    _extract_total_metrics,
    parse_latest_result,
)

HISTORY_NUM = 10


def write_to_gh_summary(line: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", None)
    if not summary_path:
        return
    with open(summary_path, "a") as f:
        f.write(line + "\n")


@dataclass_json
@dataclass
class StdoutResult:
    total: TotalMetrics
    agents: dict[str, AgentMetrics]


@dataclass_json
@dataclass
class HistoryResult(DataClassJsonMixin):
    timestamp: str
    stdout_results: StdoutResult
    ci_results: CIResult


def _write_total_summary(
    f,
    total_metrics: TotalMetrics,
    recent_total_metrics: TotalMetrics | None,
    avg_total_metrics: TotalMetrics | None,
) -> None:
    """
    Write the total usage summary table.
    """
    f.write("## LLM Usage Summary\n\n")
    f.write(f"**Total Execution Time**: {total_metrics.execution_time}\n\n")
    if recent_total_metrics is None or avg_total_metrics is None:
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        f.write(f"| Tokens Used | {total_metrics.tokens_used} |\n")
        f.write(f"| Prompt Tokens | {total_metrics.prompt_tokens} |\n")
        f.write(f"| Completion Tokens | {total_metrics.completion_tokens} |\n")
        f.write(f"| Successful Requests | {total_metrics.successful_requests} |\n")
        f.write(f"| Total Cost (USD) | ${total_metrics.total_cost} |\n")
        f.write(f"| Cache Savings (USD) | ${total_metrics.cache_savings} |\n\n")
    else:
        f.write("| Metric | Value | Recent | Avg |\n")
        f.write("|--------|-------|--------|-----|\n")
        f.write(
            f"| Tokens Used | {total_metrics.tokens_used} |"
            f" {recent_total_metrics.tokens_used} | {avg_total_metrics.tokens_used} |\n"
        )
        f.write(
            f"| Prompt Tokens | {total_metrics.prompt_tokens} |"
            f" {recent_total_metrics.prompt_tokens} |"
            f" {avg_total_metrics.prompt_tokens} |\n"
        )
        f.write(
            f"| Completion Tokens | {total_metrics.completion_tokens} |"
            f" {recent_total_metrics.completion_tokens} |"
            f" {avg_total_metrics.completion_tokens} |\n"
        )
        f.write(
            f"| Successful Requests | {total_metrics.successful_requests} |"
            f" {recent_total_metrics.successful_requests} |"
            f" {avg_total_metrics.successful_requests} |\n"
        )
        f.write(
            f"| Total Cost (USD) | ${total_metrics.total_cost} |"
            f" ${recent_total_metrics.total_cost} |"
            f" ${avg_total_metrics.total_cost} |\n"
        )
        f.write(
            f"| Cache Savings (USD) | ${total_metrics.cache_savings} |"
            f" ${recent_total_metrics.cache_savings} |"
            f" ${avg_total_metrics.cache_savings} |\n\n"
        )


def _write_agent_comparison(f, agent_metrics: dict[str, AgentMetrics]) -> None:
    """
    Write the agent comparison table.
    """
    f.write("## Agent Comparison\n\n")
    f.write(
        "| Agent | Instances | Total Exec Time | Avg Exec Time | Tokens Used | Prompt"
        " Tokens | Completion Tokens | Requests | Cost (USD) | Cache Savings (USD) |\n"
    )
    f.write(
        "|-------|-----------|----------------|---------------|"
        "-------------|---------------|-------------------|"
        "----------|------------|-------------------|\n"
    )

    for agent, metrics in agent_metrics.items():
        f.write(
            f"| {agent} | {metrics.number_of_instances} |"
            f" {metrics.total_execution_time} | {metrics.average_execution_time} |"
            f" {metrics.tokens_used} | {metrics.prompt_tokens} |"
            f" {metrics.completion_tokens} | {metrics.successful_requests} |"
            f" ${metrics.total_cost} | ${metrics.cache_savings} |\n"
        )

    f.write("\n")


def _write_combined_chart(
    f,
    title: str,
    current_metrics: dict[str, AgentMetrics],
    recent_metrics: dict[str, AgentMetrics] | None = None,
    avg_metrics: dict[str, AgentMetrics] | None = None,
    value_getter: Callable[[AgentMetrics], float] = lambda x: x.tokens_used,
    format_value: Callable[[float], str] = str,
) -> None:
    """Write a combined ASCII chart with different lines for current, recent,
    and average metrics."""
    f.write(f"## {title}\n\n")
    f.write("```\n")

    all_agents = sorted(set(current_metrics.keys()))

    max_value: float = 0
    for metrics in [current_metrics, recent_metrics, avg_metrics]:
        if metrics:
            max_value = max(max_value, max(value_getter(m) for m in metrics.values()))

    scale_factor = 50 / max_value if max_value > 0 else 1

    for agent in all_agents:
        f.write(f"{agent}:\n")

        current_val = value_getter(current_metrics[agent])
        current_len = int(current_val * scale_factor)
        f.write(f"  Current: {'█' * current_len} {format_value(current_val)}\n")

        if recent_metrics and agent in recent_metrics:
            recent_val = value_getter(recent_metrics[agent])
            recent_len = int(recent_val * scale_factor)
            f.write(f"  Recent : {'▓' * recent_len} {format_value(recent_val)}\n")

        if avg_metrics and agent in avg_metrics:
            avg_val = value_getter(avg_metrics[agent])
            avg_len = int(avg_val * scale_factor)
            f.write(f"  Average: {'░' * avg_len} {format_value(avg_val)}\n")

        f.write("\n")

    f.write("```\n\n")


def _write_stdout_summary_to_github(
    total_metrics: TotalMetrics,
    agent_metrics: dict[str, AgentMetrics],
    recent_stdout_results: StdoutResult | None,
    avg_stdout_results: StdoutResult | None,
) -> None:
    """
    Write results to GitHub Actions summary.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    recent_total_metrics = (
        recent_stdout_results.total if recent_stdout_results is not None else None
    )
    recent_agent_metrics = (
        recent_stdout_results.agents if recent_stdout_results is not None else None
    )
    avg_total_metrics = (
        avg_stdout_results.total if avg_stdout_results is not None else None
    )
    avg_agent_metrics = (
        avg_stdout_results.agents if avg_stdout_results is not None else None
    )

    with open(summary_path, "a") as f:
        # Total summary table
        _write_total_summary(f, total_metrics, recent_total_metrics, avg_total_metrics)

        # Agent comparison table
        _write_agent_comparison(f, agent_metrics)

        # Execution time ASCII chart
        _write_combined_chart(
            f,
            "Total Execution Time by Agent",
            agent_metrics,
            recent_agent_metrics,
            avg_agent_metrics,
            value_getter=lambda x: x.total_execution_time,
            format_value=lambda x: f"{x:.2f} secs",
        )

        # Average execution time ASCII chart
        _write_combined_chart(
            f,
            "Average Execution Time by Agent",
            agent_metrics,
            recent_agent_metrics,
            avg_agent_metrics,
            value_getter=lambda x: x.average_execution_time,
            format_value=lambda x: f"{x:.2f} secs",
        )

        # Token usage ASCII chart
        _write_combined_chart(
            f,
            "Token Usage by Agent",
            agent_metrics,
            recent_agent_metrics,
            avg_agent_metrics,
            value_getter=lambda x: x.tokens_used,
            format_value=lambda x: f"{x:.5f}",
        )

        # Cost ASCII chart
        _write_combined_chart(
            f,
            "Cost by Agent",
            agent_metrics,
            recent_agent_metrics,
            avg_agent_metrics,
            value_getter=lambda x: x.total_cost,
            format_value=lambda x: f"${x:.5f}",
        )


def parse_stdout(
    stdout: Path,
) -> StdoutResult:
    """
    Parses the stdout of a CI job and returns a dictionary with the results.
    """

    with open(stdout, "r") as f:
        content = f.read()

    # Parse total metrics
    try:
        total_metrics = _extract_total_metrics(content)
    except ValueError:
        print(f"❌ No total metrics found in {stdout}. Removing file.")
        # remove .ci-stdout file
        stdout.unlink()
        raise

    # Parse agent metrics
    try:
        agent_metrics = _extract_agent_metrics(content)
    except ValueError:
        print(f"❌ No agent metrics found in {stdout}. Removing file.")
        # remove .ci-stdout file
        stdout.unlink()
        raise

    results: StdoutResult = StdoutResult(
        total=total_metrics,
        agents=agent_metrics,
    )

    return results


def write_ci_result_summaries_to_github(
    results: CIResult, past_ci_results: list[tuple[CIResult, str]]
) -> None:
    """
    Writes current and past results summaries to GitHub.

    Args:
        results: Current CI results
        past_ci_results: List of past CI results
    """
    # Write current results
    _write_result_summary_to_github("MLLA Execution Results Summary", results)

    # Write past results
    for i, (past_ci_result, timestamp) in enumerate(past_ci_results):
        write_to_gh_summary("<details>")
        write_to_gh_summary(f"<summary>Past CI Results - [{i}] {timestamp}</summary>")
        _write_result_summary_to_github("", past_ci_result)
        write_to_gh_summary("</details>")


def validate_ci_results(
    results: CIResult, past_ci_results: list[tuple[CIResult, str]], all_pass: bool
) -> None:
    """
    Validates the results against requirements.

    Args:
        results: Current CI results
        past_ci_results: List of past CI results
        all_pass: If true, the CI job is assumed to have passed

    Raises:
        Exception: If validation fails
    """
    # Check if all harnesses are exploited
    all_exploited, failed_harnesses = _check_all_harnesses_exploited(
        results.harness_status
    )

    # Handle all_pass mode
    if all_pass and not all_exploited:
        msg = "❌ Not all harnesses are exploited; CI failed\n\n"
        msg += f"Failed harnesses: {failed_harnesses}\n\n"
        raise Exception(msg)

    # Compare with previous results if available
    if not all_pass and len(past_ci_results) > 0:
        current_exploited = count_exploited_harnesses(results.harness_status)
        previous_exploited = count_exploited_harnesses(
            past_ci_results[-1][0].harness_status
        )

        if current_exploited < previous_exploited:
            raise Exception("❌ Less exploited harnesses in this CI run")


def count_exploited_harnesses(harness_status: dict[str, HarnessStatus]) -> int:
    """
    Counts the number of exploited harnesses.

    Args:
        harness_status: Dictionary of harness statuses

    Returns:
        Number of exploited harnesses
    """
    return sum(1 for status in harness_status.values() if status.exploited)


def _check_all_harnesses_exploited(
    harness_status: dict[str, HarnessStatus],
) -> tuple[bool, list[str]]:
    """Check if all harnesses are exploited."""
    failed_harnesses = []
    for harness_name, value in harness_status.items():
        if not value.exploited:
            failed_harnesses.append(harness_name)
    return len(failed_harnesses) == 0, failed_harnesses


def _write_result_summary_to_github(title: str, results: CIResult) -> None:
    """Write formatted results to GitHub summary."""
    # Main header
    if title:
        write_to_gh_summary(f"## {title}")

    # Blob statistics section
    write_to_gh_summary("### Blob Statistics")
    write_to_gh_summary(f"- **Succeeded**: {results.blob_stats.succeeded}")
    write_to_gh_summary(f"- **Failed**: {results.blob_stats.failed}")
    write_to_gh_summary(f"- **Total**: {results.blob_stats.total}")

    # Harness status section
    _write_harness_status_summary(results.harness_status)

    # Sanitizer results section
    if results.sanitizer_results:
        _write_sanitizer_results_summary(results.sanitizer_results)

    # Final status
    all_exploited, failed_harnesses = _check_all_harnesses_exploited(
        results.harness_status
    )
    if all_exploited:
        write_to_gh_summary("### ✅ All harnesses are exploited")
    else:
        write_to_gh_summary("### ❌ Not all harnesses are exploited")
        write_to_gh_summary("Failed harnesses:")
        for harness_name in failed_harnesses:
            write_to_gh_summary(f"- {harness_name}")


def _write_harness_status_summary(harness_status: dict[str, HarnessStatus]) -> None:
    """Write harness status section to GitHub summary."""
    write_to_gh_summary("### Harness Status")
    for harness_name, status in harness_status.items():
        exploited = "✅ Success" if status.exploited else "❌ Failed"
        write_to_gh_summary(f"#### {harness_name}")
        write_to_gh_summary(f"- **Exploited**: {exploited}")
        write_to_gh_summary(f"- **Successful Blobs**: {status.successful_blobs}")
        write_to_gh_summary(f"- **Total Blobs**: {status.total_blobs}")


def _write_sanitizer_results_summary(
    sanitizer_results: dict[str, list[BlobInfo]],
) -> None:
    """Write sanitizer results section to GitHub summary."""
    write_to_gh_summary("### Sanitizer Results")
    for sanitizer_type, blobs in sanitizer_results.items():
        if not blobs:
            continue
        write_to_gh_summary(f"#### {sanitizer_type}")
        for blob_info in blobs:
            _write_blob_info(blob_info)


def _write_blob_info(blob_info: BlobInfo) -> None:
    """Format and write blob information."""

    blob_path = blob_info.blob
    harness = blob_info.harness

    write_to_gh_summary(f"- **Blob**: {Path(blob_path).resolve()}")
    write_to_gh_summary(f"  - **Harness**: {harness}")


def save_results_as_artifact(stdout_results: StdoutResult, ci_results: CIResult):
    """Save results as a JSON file to be uploaded as a GitHub Artifact."""
    results_dir = Path("ci-results")
    results_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    results_file = results_dir / f"results-{timestamp}.json"

    combined_results: HistoryResult = HistoryResult(
        timestamp=timestamp,
        stdout_results=stdout_results,
        ci_results=ci_results,
    )

    with open(results_file, "w") as f:
        f.write(combined_results.to_json(indent=2))

    # Update history file
    history_file = results_dir / "history.json"
    history = []

    if history_file.exists():
        with open(history_file, "r") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []

    history.append(combined_results.to_dict())
    # Keep only the latest 10 results
    history = history[-HISTORY_NUM:]

    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)

    return results_file, history_file


def get_historic_ci_results(
    prev_results_dir: Path,
) -> list[tuple[CIResult, str]]:
    """Get the history of CI results from the history file."""
    history_file = prev_results_dir / "history.json"
    if not history_file.exists():
        return []

    with open(history_file, "r") as f:
        data = json.load(f)

    history: list[HistoryResult] = [HistoryResult.from_dict(item) for item in data]

    return list(reversed([(item.ci_results, item.timestamp) for item in history[-5:]]))


def get_historic_stdout_results(
    prev_results_dir: Path,
) -> Optional[tuple[StdoutResult, StdoutResult]]:
    """Get the history of stdout results from the history file."""
    history_file = prev_results_dir / "history.json"
    if not history_file.exists():
        return None

    with open(history_file, "r") as f:
        data = json.load(f)

    history: list[HistoryResult] = [HistoryResult.from_dict(item) for item in data]

    # Return two results:
    # 1. The latest stdout result
    # 2. The average of the stdout results
    # stdout results is a dict of dicts, with the two keys:
    # - "total": a dict of total metrics
    #   - "tokens_used": int
    #   - "prompt_tokens": int
    #   - "completion_tokens": int
    #   - "successful_requests": int
    #   - "total_cost": float
    #   - "execution_time": float
    # - "agents": a dict of dict of agent metrics
    #   - agent_name: a dict of agent metrics
    #       - "execution_time": float
    #       - "tokens_used": int
    #       - "prompt_tokens": int
    #       - "completion_tokens": int
    #       - "successful_requests": int
    #       - "total_cost": float

    latest_stdout_result: StdoutResult = history[-1].stdout_results

    total_metrics = TotalMetrics.create_empty()
    agent_metrics = {}

    for result in history:
        stdout_results: StdoutResult = result.stdout_results
        for agent, metrics in stdout_results.agents.items():
            if agent not in agent_metrics:
                agent_metrics[agent] = metrics
            else:
                agent_metrics[agent].add(metrics)
        total_metrics.add(stdout_results.total)

    for agent in agent_metrics:
        agent_metrics[agent].divide(len(history))
    total_metrics.divide(len(history))

    avg_stdout_result: StdoutResult = StdoutResult(
        total=total_metrics,
        agents=agent_metrics,
    )

    return latest_stdout_result, avg_stdout_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Parse the stdout of a CI job and return a dictionary with the results."
        )
    )
    parser.add_argument(
        "stdout",
        type=str,
        help="The stdout of a CI job",
        metavar="STDOUT",
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="The results directory",
        metavar="RESULTS_DIR",
    )
    parser.add_argument(
        "cp",
        type=str,
        help="CP name",
        metavar="CP",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        help="The start time of the CI job",
        metavar="START_TIME",
    )
    parser.add_argument(
        "--all-pass",
        action="store_true",
        help="If true, the CI job is assumed to have passed",
    )
    parser.add_argument(
        "--harness",
        type=str,
        help="The harness name",
        metavar="HARNESS",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="If true, this is a standalone evaluation",
    )
    args = parser.parse_args()
    stdout = Path(args.stdout)
    no_stdout = False
    res = get_historic_stdout_results(Path("ci-results"))
    if res is None:
        print("❌ No history file found")
        recent_stdout_results = None
        avg_stdout_results = None
    else:
        recent_stdout_results, avg_stdout_results = res
        print(f"✅ Recent stdout results: {pprint.pformat(recent_stdout_results)}")
        print(f"✅ Avg stdout results: {pprint.pformat(avg_stdout_results)}")

    if stdout.exists():
        stdout_results = parse_stdout(stdout)
        _write_stdout_summary_to_github(
            stdout_results.total,
            stdout_results.agents,
            recent_stdout_results,
            avg_stdout_results,
        )
    else:
        no_stdout = True

    past_ci_results = get_historic_ci_results(Path("ci-results"))
    ci_results = parse_latest_result(
        args.results_dir,
        f"{args.cp}-{args.harness}" if args.harness else args.cp,
        start_time=args.start_time,
        is_standalone=args.standalone,
    )
    # Write summary to GitHub
    write_ci_result_summaries_to_github(ci_results, past_ci_results)

    if not args.start_time and no_stdout:
        print("❌ No stdout file found")
        raise Exception("No stdout file found")

    results_file, history_file = save_results_as_artifact(stdout_results, ci_results)
    print(f"✅ Results saved to: {results_file}")
    print(f"✅ History saved to: {history_file}")

    # Validate results after saving to artifacts
    validate_ci_results(ci_results, past_ci_results, args.all_pass)

    exit(0)
