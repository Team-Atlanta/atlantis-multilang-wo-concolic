from typing import Callable, List

from .agent import SNAPSHOT_AGENTS
from .context import GlobalContext


def calculate_execution_time_from_instances(gc, agent_instances):
    """Calculate total and average execution time from agent instances."""
    total_seconds = 0
    instance_durations = []

    for instance_id in agent_instances:
        snapshot = gc.general_callback.get_usage_between_snapshots(
            f"{instance_id}_start", f"{instance_id}_end"
        )
        if snapshot and hasattr(snapshot, "duration"):
            instance_durations.append(snapshot.duration)

    avg_duration = None
    if instance_durations:
        total_seconds = sum(d.total_seconds() for d in instance_durations)
        avg_duration = total_seconds / len(instance_durations)

    return total_seconds, avg_duration, len(instance_durations)


def aggregate_metrics(metrics1, metrics2):
    """Aggregate two metrics objects."""
    if metrics1 is None:
        return metrics2
    if metrics2 is None:
        return metrics1

    # Handle both dict and object types
    if isinstance(metrics1, dict):
        return {
            "requests": metrics1["requests"] + metrics2["requests"],
            "total_tokens": metrics1["total_tokens"] + metrics2["total_tokens"],
            "prompt_tokens": metrics1["prompt_tokens"] + metrics2["prompt_tokens"],
            "completion_tokens": (
                metrics1["completion_tokens"] + metrics2["completion_tokens"]
            ),
            "cost": metrics1["cost"] + metrics2["cost"],
            "cache_savings": metrics1["cache_savings"] + metrics2["cache_savings"],
        }
    else:
        # Assume ModelUsage object with + operator
        return metrics1 + metrics2


def collect_snapshot_metrics(gc, agent_instances):
    """Collect and aggregate metrics from snapshot-based agent instances."""
    total_metrics = None
    total_seconds = 0
    instance_count = 0

    for instance_id in agent_instances:
        snapshot = gc.general_callback.get_usage_between_snapshots(
            f"{instance_id}_start", f"{instance_id}_end"
        )
        if not snapshot:
            continue

        total_metrics = aggregate_metrics(total_metrics, snapshot.total_usage)
        total_seconds += snapshot.duration.total_seconds()
        instance_count += 1

    return total_metrics, total_seconds, instance_count


def collect_snapshot_model_usage(gc, agent_instances):
    """Collect and aggregate per-model usage from snapshot-based agent instances."""
    model_usage_agg = {}

    for instance_id in agent_instances:
        snapshot = gc.general_callback.get_usage_between_snapshots(
            f"{instance_id}_start", f"{instance_id}_end"
        )
        if not snapshot or not hasattr(snapshot, "model_usage"):
            continue

        for model, model_metrics in snapshot.model_usage.items():
            if model_metrics.requests <= 0:
                continue

            if model not in model_usage_agg:
                model_usage_agg[model] = model_metrics
            else:
                model_usage_agg[model] = aggregate_metrics(
                    model_usage_agg[model], model_metrics
                )

    return model_usage_agg


def display_agent_metrics(
    gc: GlobalContext,
    agent_names: List[str],
    output_func: Callable[[str], None],
    include_per_model: bool = True,
):
    """Display comprehensive agent metrics with total, per-model, and per-agent.

    Args:
        gc: GlobalContext containing the callback handler and execution info
        agent_names: List of agent names to display metrics for
        output_func: Function to call with the formatted output string
        include_per_model: Whether to include per-model usage breakdown
    """
    result = []

    def print_section_header(title):
        """Print a section header with consistent formatting."""
        result.append("")
        result.append(f"=== {title} ===")

    def print_usage_metrics(prefix, metrics):
        """Print usage metrics with consistent formatting."""
        if isinstance(metrics, dict):
            result.append(f"{prefix}Successful Requests: {metrics['requests']}")
            result.append(f"{prefix}Total Tokens: {metrics['total_tokens']}")
            result.append(f"{prefix}Input Tokens: {metrics['prompt_tokens']}")
            result.append(f"{prefix}Output Tokens: {metrics['completion_tokens']}")
            result.append(f"{prefix}Total Cost: ${metrics['cost']:.4f}")
            result.append(f"{prefix}Cache Savings: ${metrics['cache_savings']:.4f}")
        else:
            result.append(f"{prefix}Successful Requests: {metrics.requests}")
            result.append(f"{prefix}Total Tokens: {metrics.total_tokens}")
            result.append(f"{prefix}Input Tokens: {metrics.prompt_tokens}")
            result.append(f"{prefix}Output Tokens: {metrics.completion_tokens}")
            result.append(f"{prefix}Total Cost: ${metrics.cost:.4f}")
            result.append(f"{prefix}Cache Savings: ${metrics.cache_savings:.4f}")

    # 1. Display overall usage summary
    print_section_header("Overall Usage Summary")
    result.append("Total Usage:")
    result.append(f"  Execution Time: {gc.get_execution_time():.2f} secs")
    print_usage_metrics("  ", gc.general_callback.total_usage)

    # 2. Display per-model usage
    if include_per_model:
        model_usage = gc.general_callback.model_usage
        if model_usage:
            print_section_header("Per-Model Usage")
            for model_name, usage in model_usage.items():
                if usage.requests > 0:
                    result.append(f"Model: {model_name}")
                    print_usage_metrics("  ", usage)

    # 3. Display per-agent metrics
    print_section_header("Agent Metrics")
    for agent_name in agent_names:
        # Handle snapshot-based agents (top-level agents with nested calls)
        if agent_name in SNAPSHOT_AGENTS:
            agent_instances = gc.general_callback.find_agent_instances(agent_name)
            if not agent_instances:
                continue

            total_metrics, total_duration, instance_count = collect_snapshot_metrics(
                gc, agent_instances
            )
            if not total_metrics:
                continue

            avg_duration = total_duration / instance_count
            result.append(f"{agent_name.upper()} Agent:")
            result.append(f"  Number of Instances: {instance_count}")
            result.append(f"  Total Execution Time: {total_duration:.2f} secs")
            result.append(f"  Average Execution Time: {avg_duration:.2f} secs")
            print_usage_metrics("  ", total_metrics)

            # Display per-model usage for snapshot agents
            if include_per_model:
                model_usage_agg = collect_snapshot_model_usage(gc, agent_instances)
                if model_usage_agg:
                    result.append(f"  {agent_name.upper()} Per-Model Usage:")
                    for model, model_metrics in model_usage_agg.items():
                        result.append(f"    Model: {model}")
                        print_usage_metrics("      ", model_metrics)
            continue

        # Handle per-agent usage tracking (leaf-level agents)
        agent_usage = gc.general_callback.get_agent_usage(agent_name)
        if not agent_usage:
            continue

        # Calculate execution time from instances
        agent_instances = gc.general_callback.find_agent_instances(agent_name)
        total_duration, avg_duration, instance_count = (
            calculate_execution_time_from_instances(gc, agent_instances)
        )

        result.append(f"{agent_name.upper()} Agent:")
        result.append(f"  Number of Instances: {instance_count}")
        result.append(f"  Total Execution Time: {total_duration:.2f} secs")
        if avg_duration:
            result.append(f"  Average Execution Time: {avg_duration:.2f} secs")

        print_usage_metrics("  ", agent_usage)

        # Display per-model usage for per-agent tracking
        if include_per_model and "model_usage" in agent_usage:
            has_model_data = False
            for model, model_metrics in agent_usage["model_usage"].items():
                if not has_model_data:
                    result.append(f"  {agent_name.upper()} Per-Model Usage:")
                    has_model_data = True
                result.append(f"    Model: {model}")
                print_usage_metrics("      ", model_metrics)

    # Output all results at once
    output_func("\n".join(result))
