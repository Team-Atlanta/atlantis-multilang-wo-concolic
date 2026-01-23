import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from dataclasses_json import DataClassJsonMixin, dataclass_json


@dataclass_json
@dataclass
class HarnessStatus:
    exploited: bool
    successful_blobs: int
    total_blobs: int


@dataclass_json
@dataclass
class BlobStats:
    succeeded: int
    failed: int
    total: int


@dataclass_json
@dataclass
class BlobInfo:
    blob: str
    harness: str


@dataclass_json
@dataclass
class CIResult(DataClassJsonMixin):
    blob_stats: BlobStats
    harness_status: dict[str, HarnessStatus]
    sanitizer_results: dict[str, list[BlobInfo]]


@dataclass_json
@dataclass
class TotalMetrics:
    tokens_used: float = 0.0
    prompt_tokens: float = 0.0
    completion_tokens: float = 0.0
    successful_requests: float = 0.0
    total_cost: float = 0.0
    execution_time: float = 0.0
    cache_savings: float = 0.0

    @staticmethod
    def create_empty() -> "TotalMetrics":
        return TotalMetrics(
            tokens_used=0,
            prompt_tokens=0,
            completion_tokens=0,
            successful_requests=0,
            total_cost=0,
            execution_time=0,
            cache_savings=0,
        )

    def add(self, other: "TotalMetrics") -> None:
        self.tokens_used += other.tokens_used
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.successful_requests += other.successful_requests
        self.total_cost += other.total_cost
        self.cache_savings += other.cache_savings

    def divide(self, val: float) -> None:
        if val == 0:
            return

        self.tokens_used /= val
        self.prompt_tokens /= val
        self.completion_tokens /= val
        self.successful_requests /= val
        self.total_cost /= val
        self.cache_savings /= val


@dataclass_json
@dataclass
class AgentMetrics:
    execution_time: float = 0.0
    tokens_used: float = 0.0
    prompt_tokens: float = 0.0
    completion_tokens: float = 0.0
    successful_requests: float = 0.0
    total_cost: float = 0.0
    cache_savings: float = 0.0
    number_of_instances: int = 1
    total_execution_time: float = 0.0
    average_execution_time: float = 0.0

    def add(self, other: "AgentMetrics") -> None:
        self.execution_time += other.execution_time
        self.tokens_used += other.tokens_used
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.successful_requests += other.successful_requests
        self.total_cost += other.total_cost
        self.cache_savings += other.cache_savings
        self.number_of_instances += other.number_of_instances
        self.total_execution_time += other.total_execution_time
        # Average will be recalculated after adding

    def divide(self, val: float) -> None:
        if val == 0:
            return

        self.execution_time /= val
        self.tokens_used /= val
        self.prompt_tokens /= val
        self.completion_tokens /= val
        self.successful_requests /= val
        self.total_cost /= val
        self.cache_savings /= val
        self.total_execution_time /= val
        self.average_execution_time /= val


def _extract_total_metrics(content: str) -> TotalMetrics:
    """
    Extract total metrics information from the new log format.
    """
    # Extract total usage section with execution time
    # fmt: off
    total_pattern = (
        r"=== Overall Usage Summary ===\s*\n"
        r"Total Usage:\s*\n"
        r"\s*Execution Time: ([\d\.]+) secs\s*\n"
        r"\s*Successful Requests: (\d+)\s*\n"
        r"\s*Total Tokens: (\d+)\s*\n"
        r"\s*Input Tokens: (\d+)\s*\n"
        r"\s*Output Tokens: (\d+)\s*\n"
        r"\s*Total Cost: \$([\d\.]+)\s*\n"
        r"\s*Cache Savings: \$([-\d\.]+)"
    )
    # fmt: on
    total_match = re.search(total_pattern, content)
    if total_match:
        total_metrics: TotalMetrics = TotalMetrics(
            execution_time=float(total_match.group(1)),
            successful_requests=int(total_match.group(2)),
            tokens_used=int(total_match.group(3)),
            prompt_tokens=int(total_match.group(4)),
            completion_tokens=int(total_match.group(5)),
            total_cost=float(total_match.group(6)),
            cache_savings=float(total_match.group(7)),
        )
        return total_metrics

    raise ValueError("No total metrics found in the log content")


def _extract_agent_metrics(content: str) -> dict[str, AgentMetrics]:
    """
    Extract metrics information for each agent from the new log format.
    """
    agent_metrics: dict[str, AgentMetrics] = {}

    # fmt: off
    # Pattern for new format with number of instances and execution times
    agent_pattern = (
        r"(\w+) Agent:\s*\n"
        r"\s*Number of Instances: (\d+)\s*\n"
        r"\s*Total Execution Time: ([\d\.]+) secs\s*\n"
        r"\s*Average Execution Time: ([\d\.]+) secs\s*\n"
        r"\s*Successful Requests: (\d+)\s*\n"
        r"\s*Total Tokens: (\d+)\s*\n"
        r"\s*Input Tokens: (\d+)\s*\n"
        r"\s*Output Tokens: (\d+)\s*\n"
        r"\s*Total Cost: \$([\d\.]+)\s*\n"
        r"\s*Cache Savings: \$([-\d\.]+)"
    )
    # fmt: on

    # Extract agents with new format (number of instances and execution times)
    for match in re.finditer(agent_pattern, content):
        agent_name = match.group(1)
        num_instances = int(match.group(2))
        total_exec_time = float(match.group(3))
        avg_exec_time = float(match.group(4))

        agent_metrics[agent_name] = AgentMetrics(
            number_of_instances=num_instances,
            total_execution_time=total_exec_time,
            average_execution_time=avg_exec_time,
            execution_time=avg_exec_time,  # For backward compatibility
            successful_requests=int(match.group(5)),
            tokens_used=int(match.group(6)),
            prompt_tokens=int(match.group(7)),
            completion_tokens=int(match.group(8)),
            total_cost=float(match.group(9)),
            cache_savings=float(match.group(10)),
        )

    if not agent_metrics:
        raise ValueError("No agent metrics found in the log content")

    return agent_metrics


def find_latest_result_file(
    results_dir: Path,
    cp: str,
    start_time: Optional[str] = None,
    is_standalone: bool = False,
) -> Path:
    """
    Finds the latest result file in the specified directory.

    Args:
        results_dir: Directory containing results
        cp: CP name
        start_time: Optional start time to filter results
        is_standalone: Whether this is a standalone evaluation

    Returns:
        Path to the latest result file

    Raises:
        FileNotFoundError: If no result files are found
    """
    # For standalone mode, append -standalone to the CP path
    if is_standalone:
        cp = f"{cp}-standalone"

    cp_result_path = results_dir / cp
    result_files = list(cp_result_path.glob("mlla-result-*.yaml"))

    if not result_files:
        error_msg = f"❌ Error: No result files found in {cp_result_path}"
        raise FileNotFoundError(error_msg)

    # Filter by start time if provided
    if start_time:
        start_datetime = datetime.strptime(start_time, "%Y-%m-%d_%H-%M-%S")
        result_files = [
            f
            for f in result_files
            if _get_datetime_from_filename(f.name) >= start_datetime
        ]

        if not result_files:
            error_msg = (
                f"❌ Error: No result files found after {start_time} in"
                f" {cp_result_path}"
            )
            raise FileNotFoundError(error_msg)

    # Get the latest file
    return max(
        result_files,
        key=lambda f: _get_datetime_from_filename(f.name),
    )


def _get_datetime_from_filename(filename: str) -> datetime:
    """Extract datetime from filename pattern."""
    m = re.search(r"mlla-result-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", filename)
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
    else:
        return datetime.strptime("1970-01-01_00-00-00", "%Y-%m-%d_%H-%M-%S")


def parse_latest_result(
    results_dir: Path,
    cp: str,
    start_time: Optional[str] = None,
    is_standalone: bool = False,
) -> CIResult:
    """
    Checks the latest result file in the results directory and processes it.

    Args:
        results_dir: Directory containing results
        cp: CP name
        start_time: Optional start time to filter results
        is_standalone: Whether this is a standalone evaluation

    Returns:
        Dictionary with processed results

    Raises:
        FileNotFoundError: If no result files are found in the specified directory
    """
    # Task 1: Find the latest result file
    latest_file = find_latest_result_file(results_dir, cp, start_time, is_standalone)
    print(f"✅ Found latest result file: {latest_file}")

    # Task 2: Load and process results
    with open(latest_file, "r") as f:
        data = yaml.safe_load(f)
    results = CIResult.from_dict(data)

    return results
