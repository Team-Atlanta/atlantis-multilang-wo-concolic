import datetime
import threading
import traceback
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

import tokencost
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from loguru import logger
from tokencost import calculate_cost_by_tokens


@dataclass
class ModelUsage:
    """Data class for storing model usage information."""

    requests: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    cache_savings: float = 0.0

    def __str__(self) -> str:
        """Return a string representation of the model usage."""
        return (
            f"Total Tokens: {self.total_tokens}\n"
            f"Input Tokens: {self.prompt_tokens}\n"
            f"Output Tokens: {self.completion_tokens}\n"
            f"Successful Requests: {self.requests}\n"
            f"Total Cost (USD): ${self.cost:.6f}\n"
            f"Cache Savings (USD): ${self.cache_savings:.6f}"
        )

    def pretty_print(self, indent=0) -> str:
        """Format the string representation with the specified indentation."""
        tabs = "  " * indent
        usage_str = str(self)

        # Add indentation to all lines including the first one
        lines = usage_str.split("\n")
        indented_lines = [tabs + line for line in lines]

        return "\n".join(indented_lines)

    def copy(self) -> "ModelUsage":
        """Create a deep copy of this ModelUsage object."""
        return ModelUsage(
            requests=self.requests,
            total_tokens=self.total_tokens,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cost=self.cost,
            cache_savings=self.cache_savings,
        )

    def __add__(self, other: "ModelUsage") -> "ModelUsage":
        """Add two ModelUsage objects together."""
        return ModelUsage(
            requests=self.requests + other.requests,
            total_tokens=self.total_tokens + other.total_tokens,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost=self.cost + other.cost,
            cache_savings=self.cache_savings + other.cache_savings,
        )

    @staticmethod
    def diff(start: "ModelUsage", end: "ModelUsage") -> "ModelUsage":
        """Calculate the difference between two ModelUsage objects."""
        return ModelUsage(
            requests=end.requests - start.requests,
            total_tokens=end.total_tokens - start.total_tokens,
            prompt_tokens=end.prompt_tokens - start.prompt_tokens,
            completion_tokens=end.completion_tokens - start.completion_tokens,
            cost=end.cost - start.cost,
            cache_savings=end.cache_savings - start.cache_savings,
        )


def calculate_token_cost(token_usage: Dict[str, Any], model_id: str) -> float:
    """Calculate the cost of tokens including cache handling."""
    try:
        # Extract token counts
        prompt_tokens = token_usage.get("input_tokens", 0)
        completion_tokens = token_usage.get("output_tokens", 0)
        input_token_details = token_usage.get("input_token_details", {})
        cache_read = input_token_details.get("cache_read", 0)
        cache_creation = input_token_details.get("cache_creation", 0)

        # Calculate regular input cost (excluding cached tokens)
        regular_input_tokens = prompt_tokens - cache_read - cache_creation

        regular_costs = calculate_full_token_cost(
            regular_input_tokens, completion_tokens, model_id
        )

        # Calculate cache costs
        cache_costs = calculate_cache_costs(cache_read, cache_creation, model_id)

        # Sum all costs
        total_cost = regular_costs + cache_costs

        return float(total_cost)

    except Exception:
        error_msg = traceback.format_exc()
        logger.warning(f"{model_id}: {error_msg}")

        return 0


def calculate_full_token_cost(
    prompt_tokens: int, completion_tokens: int, model_id: str
) -> float:
    """Calculate the full token cost without considering cache."""
    try:
        # Calculate input cost
        input_cost = 0
        if prompt_tokens > 0:
            input_cost = calculate_cost_by_tokens(prompt_tokens, model_id, "input")

        # Calculate output cost
        output_cost = calculate_cost_by_tokens(completion_tokens, model_id, "output")

        # Sum costs
        total_cost = float(Decimal(input_cost) + Decimal(output_cost))

        return total_cost

    except Exception:
        error_msg = traceback.format_exc()
        logger.warning(f"{model_id}: {error_msg}")

        return 0


def calculate_cache_costs(cache_read: int, cache_creation: int, model_id: str) -> float:
    """Calculate total costs for cache read and creation operations."""
    try:
        cache_read_cost = 0.0
        cache_creation_cost = 0.0

        if model_id:
            # Check if this is a Claude model that needs special 1h cache handling
            if model_id.startswith("claude"):
                cache_read_cost, cache_creation_cost = calculate_claude_cache_costs(
                    cache_read, cache_creation, model_id
                )
            else:
                # Use existing TOKEN_COST logic for non-Claude models
                model_info = tokencost.TOKEN_COSTS.get(model_id, {})

                # Calculate cache read cost
                if cache_read > 0:
                    cache_read_cost_per_token = model_info.get(
                        "cache_read_input_token_cost"
                    )
                    if cache_read_cost_per_token:
                        cache_read_cost = cache_read * cache_read_cost_per_token

                # Calculate cache creation cost
                if cache_creation > 0:
                    cache_creation_cost_per_token = model_info.get(
                        "cache_creation_input_token_cost"
                    )
                    if cache_creation_cost_per_token:
                        cache_creation_cost = (
                            cache_creation * cache_creation_cost_per_token
                        )

        # Return the sum of both costs
        return float(Decimal(cache_read_cost) + Decimal(cache_creation_cost))

    except Exception:
        error_msg = traceback.format_exc()
        logger.warning(f"{model_id}: {error_msg}")

        return 0.0


def calculate_claude_cache_costs(
    cache_read: int, cache_creation: int, model_id: str
) -> tuple[float, float]:
    """Calculate cache costs specifically for Claude models using 1h cache pricing."""
    # Claude model pricing per million tokens (1h cache)
    CLAUDE_CACHE_PRICING = {
        "claude-sonnet-4-20250514": {
            "cache_hits": 0.30 / 1_000_000,  # $0.30 / MTok
            "cache_creation_1h": 6.0 / 1_000_000,  # $6 / MTok
        },
        "claude-opus-4-20250514": {
            "cache_hits": 1.50 / 1_000_000,  # $1.50 / MTok
            "cache_creation_1h": 30.0 / 1_000_000,  # $30 / MTok
        },
        "claude-3-7-sonnet-20250219": {
            "cache_hits": 0.30 / 1_000_000,  # $0.30 / MTok
            "cache_creation_1h": 6.0 / 1_000_000,  # $6 / MTok
        },
        "claude-3-opus-20240229": {
            "cache_hits": 1.50 / 1_000_000,  # $1.50 / MTok
            "cache_creation_1h": 30.0 / 1_000_000,  # $30 / MTok
        },
        "claude-3-5-sonnet-20241022": {
            "cache_hits": 0.30 / 1_000_000,  # $0.30 / MTok
            "cache_creation_1h": 6.0 / 1_000_000,  # $6 / MTok
        },
        "claude-3-5-haiku-20241022": {
            "cache_hits": 0.08 / 1_000_000,  # $0.08 / MTok
            "cache_creation_1h": 1.6 / 1_000_000,  # $1.6 / MTok
        },
        "claude-3-sonnet-20240229": {
            "cache_hits": 0.30 / 1_000_000,  # $0.30 / MTok
            "cache_creation_1h": 6.0 / 1_000_000,  # $6 / MTok
        },
        "claude-3-haiku-20240307": {
            "cache_hits": 0.03 / 1_000_000,  # $0.03 / MTok
            "cache_creation_1h": 0.50 / 1_000_000,  # $0.50 / MTok
        },
    }

    cache_read_cost = 0.0
    cache_creation_cost = 0.0

    # Get pricing for this specific Claude model
    pricing = CLAUDE_CACHE_PRICING.get(model_id)
    if pricing:
        # Calculate cache read cost (cache hits)
        if cache_read > 0:
            cache_read_cost = cache_read * pricing["cache_hits"]

        # Calculate cache creation cost (1h cache creation)
        if cache_creation > 0:
            cache_creation_cost = cache_creation * pricing["cache_creation_1h"]
    else:
        # Log warning for unsupported Claude model
        logger.warning(f"Claude model {model_id} not found in cache pricing table")

    return cache_read_cost, cache_creation_cost


def calculate_cache_savings(
    prompt_tokens: int,
    completion_tokens: int,
    model_id: str,
    actual_cost: float,
    token_usage: Dict[str, Any],
) -> float:
    """Calculate the savings from using cache."""
    try:
        # Extract cache information
        input_token_details = token_usage.get("input_token_details", {})
        cache_read = input_token_details.get("cache_read", 0)
        cache_creation = input_token_details.get("cache_creation", 0)

        # If no cache was used, return 0
        if cache_read == 0 and cache_creation == 0:
            return 0.0

        # Calculate cost without cache (full charge w/o cache)
        cost_without_cache = calculate_full_token_cost(
            prompt_tokens, completion_tokens, model_id
        )

        # Calculate savings (can be 0 if no cache was used)
        savings = cost_without_cache - actual_cost

        return savings

    except Exception:
        error_msg = traceback.format_exc()
        logger.warning(f"{model_id}: {error_msg}")

        return 0.0


def standardize_model_name(
    model_name: str,
) -> str:
    model_name = model_name.lower()
    if "/" in model_name:
        model_name = model_name.split("/")[-1]
    return model_name


@dataclass
class TokenUsageSnapshot:
    """Point-in-time snapshot of token usage."""

    total_usage: ModelUsage
    model_usage: Dict[str, ModelUsage] = field(default_factory=dict)
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)

    def __sub__(self, other: "TokenUsageSnapshot") -> "TokenUsageDiff":
        """Calculate the difference between two snapshots using subtraction."""
        return TokenUsageDiff.from_snapshots(other, self)

    def __repr__(self) -> str:
        usage_str = self.total_usage.pretty_print(indent=1)
        base_repr = "TokenUsageSnapshot\n"
        base_repr += f"  Current Time: {self.timestamp}\n"
        base_repr += f"{usage_str}"

        # Add per-model usage if available (only for models with requests > 0)
        used_models = {
            model: usage
            for model, usage in self.model_usage.items()
            if usage.requests > 0
        }

        if used_models:
            model_usage_str = "\n\n  Per-Model Token Usage:"
            for model, usage in used_models.items():
                usage_str = usage.pretty_print(indent=3)
                model_usage_str += f"\n    {model}:\n"
                model_usage_str += f"{usage_str}"
            return base_repr + model_usage_str

        return base_repr


@dataclass
class TokenUsageDiff:
    """Difference in token usage between two points in time."""

    total_usage: ModelUsage
    model_usage: Dict[str, ModelUsage]
    start_time: datetime.datetime
    end_time: datetime.datetime

    @property
    def duration(self) -> datetime.timedelta:
        return self.end_time - self.start_time

    @classmethod
    def from_snapshots(
        cls, start: TokenUsageSnapshot, end: TokenUsageSnapshot
    ) -> "TokenUsageDiff":
        """Calculate the difference between two snapshots."""
        # Calculate per-model usage differences
        model_usage_diff = {}
        all_models = set(start.model_usage.keys()) | set(end.model_usage.keys())

        for model in all_models:
            # Get model usage from both snapshots, or create empty ModelUsage
            start_usage = start.model_usage.get(model, ModelUsage())
            end_usage = end.model_usage.get(model, ModelUsage())

            # Calculate the difference between the two model usages
            model_usage_diff[model] = ModelUsage.diff(start_usage, end_usage)

        # Calculate the difference between the total usage of the two snapshots
        total_usage_diff = ModelUsage.diff(start.total_usage, end.total_usage)

        return cls(
            total_usage=total_usage_diff,
            model_usage=model_usage_diff,
            start_time=start.timestamp,
            end_time=end.timestamp,
        )

    def __repr__(self) -> str:
        usage_str = self.total_usage.pretty_print(indent=1)
        base_repr = "TokenUsageDiff\n"
        base_repr += f"  Execution Time: {self.duration}\n"
        base_repr += f"{usage_str}"

        # Add per-model usage if available (only for models with requests > 0)
        used_models = {
            model: usage
            for model, usage in self.model_usage.items()
            if usage.requests > 0
        }

        if used_models:
            model_usage_str = "\n\n  Per-Model Token Usage:"
            for model, usage in used_models.items():
                usage_str = usage.pretty_print(indent=3)
                model_usage_str += f"\n    {model}:\n"
                model_usage_str += f"{usage_str}"
            return base_repr + model_usage_str

        return base_repr


class BedrockTokenUsageCallbackHandler(BaseCallbackHandler):
    """Callback Handler that tracks bedrock anthropic info."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

        # Initialize instance variables instead of class variables
        self.model_names: dict[Any, str] = {}
        self.snapshots: Dict[str, TokenUsageSnapshot] = {}
        self.total_usage: ModelUsage = ModelUsage()
        self.model_usage: Dict[str, ModelUsage] = {}

        # Add agent-specific tracking
        self.agent_usage: Dict[str, ModelUsage] = {}  # agent_name -> ModelUsage
        self.agent_model_usage: Dict[str, Dict[str, ModelUsage]] = (
            {}
        )  # agent_name -> model_name -> ModelUsage
        self.run_agent_map: Dict[str, str] = {}  # run_id -> agent_name

    def __repr__(self) -> str:
        # Format the total usage information using the ModelUsage.__str__ method
        usage_str = self.total_usage.pretty_print(indent=1)
        base_repr = "Total:\n"
        base_repr = f"{usage_str}"

        # Add per-model usage if available (only for models with requests > 0)
        used_models = {
            model: usage
            for model, usage in self.model_usage.items()
            if usage.requests > 0
        }

        if used_models:
            model_usage_str = "\n\nPer-Model Token Usage:"
            for model, usage in used_models.items():
                usage_str = usage.pretty_print(indent=2)
                model_usage_str += f"\n  {model}:\n"
                model_usage_str += f"{usage_str}"
            return base_repr + model_usage_str

        return base_repr

    @property
    def always_verbose(self) -> bool:
        """Whether to call verbose callbacks even if verbose is False."""
        return True

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> None:
        """Store model name and agent name mapping."""
        try:
            run_id = kwargs.get("run_id", None)
            model_name = serialized["kwargs"]["model"]
            self.model_names[run_id] = standardize_model_name(model_name)

            # Store agent name if provided
            agent_name = kwargs.get("agent_name")
            if agent_name and run_id:
                with self._lock:
                    self.run_agent_map[run_id] = agent_name

            if agent_name:
                del kwargs["agent_name"]

        except KeyError:
            pass

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Print out the token."""
        pass

    def _extract_token_usage(
        self, response: LLMResult
    ) -> tuple[Optional[Dict[str, Any]], int, int, int, str]:
        """Extract token usage information from the response."""
        # Check for usage_metadata (langchain-core >= 0.2.2)
        try:
            generation = response.generations[0][0]
        except IndexError:
            generation = None

        # Try to extract usage metadata from ChatGeneration
        usage_metadata = None
        response_metadata = None
        if isinstance(generation, ChatGeneration):
            try:
                message = generation.message
                if isinstance(message, AIMessage):
                    usage_metadata = message.usage_metadata
                    response_metadata = message.response_metadata

            except AttributeError:
                pass

        # Process usage metadata if available
        if usage_metadata:
            token_usage = usage_metadata
            total_tokens = token_usage["total_tokens"]
            completion_tokens = token_usage["output_tokens"]
            prompt_tokens = token_usage["input_tokens"]

            # Extract model name
            if response_model_name := (response_metadata or {}).get("model_name"):
                model_name = standardize_model_name(response_model_name)
            elif response.llm_output is None:
                model_name = ""
            else:
                model_name = standardize_model_name(
                    response.llm_output.get("model_name", "")
                )
        # Fall back to llm_output
        else:
            if response.llm_output is None:
                return None, 0, 0, 0, ""

            # Handle different response formats
            if "model_name" in response.llm_output:
                if "token_usage" not in response.llm_output:
                    return None, 0, 0, 0, ""

                token_usage = response.llm_output["token_usage"]
                completion_tokens = token_usage.get("completion_tokens", 0)
                prompt_tokens = token_usage.get("prompt_tokens", 0)
                model_name = standardize_model_name(
                    response.llm_output.get("model_name", "")
                )
            else:
                if "usage" not in response.llm_output:
                    return None, 0, 0, 0, ""

                token_usage = response.llm_output["usage"]
                completion_tokens = token_usage.get("output_tokens", 0)
                prompt_tokens = token_usage.get("input_tokens", 0)
                model_name = standardize_model_name(
                    response.llm_output.get("model", None)
                )

            total_tokens = completion_tokens + prompt_tokens

        return token_usage, total_tokens, prompt_tokens, completion_tokens, model_name

    def _update_usage(
        self,
        token_usage: Dict[str, Any],
        model_name: str,
        model_usage: ModelUsage,
        agent_name: Optional[str] = None,
    ) -> None:
        """Update usage statistics with a ModelUsage object.

        Args:
            model_name: Name of the model
            model_usage: The ModelUsage object to add to the total usage
            token_usage: Token usage information
            agent_name: Optional name of the agent
        """
        # Update total usage
        self.total_usage = self.total_usage + model_usage

        # Update per-model token usage if model name is available
        if model_name:
            if model_name not in self.model_usage:
                self.model_usage[model_name] = ModelUsage()

            # Update model usage with current request data
            self.model_usage[model_name] = self.model_usage[model_name] + model_usage

        # Update agent-specific usage if agent name is provided
        if agent_name:
            # Initialize agent usage if needed
            if agent_name not in self.agent_usage:
                self.agent_usage[agent_name] = ModelUsage()

            # Update agent's total usage
            self.agent_usage[agent_name] = self.agent_usage[agent_name] + model_usage

            # Update agent's per-model usage if model name is available
            if model_name:
                if agent_name not in self.agent_model_usage:
                    self.agent_model_usage[agent_name] = {}

                if model_name not in self.agent_model_usage[agent_name]:
                    self.agent_model_usage[agent_name][model_name] = ModelUsage()

                # Update agent's per-model usage
                self.agent_model_usage[agent_name][model_name] = (
                    self.agent_model_usage[agent_name][model_name] + model_usage
                )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Collect token usage and attribute to the agent if specified."""
        # Extract token usage information
        token_usage, total_tokens, prompt_tokens, completion_tokens, model_name = (
            self._extract_token_usage(response)
        )

        # Handle case where no token usage information is available
        if token_usage is None:
            with self._lock:
                self.total_usage.requests += 1
            return None

        # Get run_id and look up agent_name
        run_id = kwargs.get("run_id")
        agent_name = None

        with self._lock:
            # Get model name from run_id if available
            model_name = self.model_names.get(run_id, model_name)

            # Get agent name from run_id if available
            if run_id in self.run_agent_map:
                agent_name = self.run_agent_map[run_id]

        # Calculate token cost
        total_cost = calculate_token_cost(
            token_usage=token_usage,
            model_id=model_name,
        )

        # Create a ModelUsage object for this request
        request_usage = ModelUsage(
            requests=1,
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=total_cost,
        )

        # Calculate cache savings if model name is available
        if model_name:
            savings = calculate_cache_savings(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model_id=model_name,
                actual_cost=total_cost,
                token_usage=token_usage,
            )

            # Add savings to the request usage
            request_usage.cache_savings = savings

        # Update usage statistics with lock to ensure thread safety
        with self._lock:
            # Update global and agent-specific usage
            self._update_usage(
                token_usage=token_usage,
                model_name=model_name,
                model_usage=request_usage,
                agent_name=agent_name,
            )

        # Extract cache information for logging
        input_token_details = token_usage.get("input_token_details", {})
        cache_read = input_token_details.get("cache_read", 0)
        cache_creation = input_token_details.get("cache_creation", 0)

        # Log agent-specific token usage
        # fmt: off
        if agent_name and model_name:
            logger.info(
                f"Agent: {agent_name} | "
                f"Model: {model_name} | "
                f"Total: {request_usage.total_tokens} | "
                f"Input: {request_usage.prompt_tokens} | "
                f"Output: {request_usage.completion_tokens} | "
                f"Cache Read: {cache_read}, Creation: {cache_creation} | "
                f"Cost: ${request_usage.cost:.4f} "
                f"(Saved ${request_usage.cache_savings:.4f})"
            )
        elif model_name:
            # Log per-model token usage with cache details
            logger.info(
                f"Model: {model_name} | "
                f"Total: {request_usage.total_tokens} | "
                f"Input: {request_usage.prompt_tokens} | "
                f"Output: {request_usage.completion_tokens} | "
                f"Cache Read: {cache_read}, Creation: {cache_creation} | "
                f"Cost: ${request_usage.cost:.4f} "
                f"(Saved ${request_usage.cache_savings:.4f})"
            )
        # fmt: on

    def create_snapshot(self, label: str) -> TokenUsageSnapshot:
        """Create a snapshot of the current token usage with a label."""
        with self._lock:
            # Create deep copies of model usage objects
            model_usage_copy = {
                model: usage.copy() for model, usage in self.model_usage.items()
            }
            total_usage_copy = self.total_usage.copy()

            snapshot = TokenUsageSnapshot(
                total_usage=total_usage_copy,
                model_usage=model_usage_copy,
            )

            self.snapshots[label] = snapshot

        return snapshot

    def get_snapshot(self, label: str) -> Optional[TokenUsageSnapshot]:
        """Get a snapshot by label."""
        return self.snapshots.get(label)

    def get_usage_between_snapshots(
        self, start_label: str, end_label: str
    ) -> Optional["TokenUsageDiff"]:
        """Get the token usage between two snapshots."""
        start_snapshot = self.get_snapshot(start_label)
        end_snapshot = self.get_snapshot(end_label)

        if not start_snapshot or not end_snapshot:
            return None

        return end_snapshot - start_snapshot

    def __copy__(self) -> "BedrockTokenUsageCallbackHandler":
        """Return a copy of the callback handler."""
        return self

    def __deepcopy__(self, memo: Any) -> "BedrockTokenUsageCallbackHandler":
        """Return a deep copy of the callback handler."""
        return self

    def get_model_usage(
        self, model_name: Optional[str] = None
    ) -> Dict[str, ModelUsage]:
        """Get token usage for a specific model or all models."""
        with self._lock:
            if model_name:
                return {model_name: self.model_usage.get(model_name, ModelUsage())}

            # Create a deep copy of the model_usage dictionary
            model_usage_copy = {
                model: usage.copy() for model, usage in self.model_usage.items()
            }
            return model_usage_copy

    def get_agent_usage(self, agent_name: str) -> Dict:
        """Get usage statistics for a specific agent without acquiring lock."""
        if agent_name not in self.agent_usage:
            return {}

        # Create a result dictionary with the agent's usage
        result: Dict = {
            "total_tokens": self.agent_usage[agent_name].total_tokens,
            "prompt_tokens": self.agent_usage[agent_name].prompt_tokens,
            "completion_tokens": self.agent_usage[agent_name].completion_tokens,
            "requests": self.agent_usage[agent_name].requests,
            "cost": self.agent_usage[agent_name].cost,
            "cache_savings": self.agent_usage[agent_name].cache_savings,
        }

        # Add per-model usage if available
        if agent_name in self.agent_model_usage:
            result["model_usage"] = {
                model: {
                    "total_tokens": usage.total_tokens,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "requests": usage.requests,
                    "cost": usage.cost,
                    "cache_savings": usage.cache_savings,
                }
                for model, usage in self.agent_model_usage[agent_name].items()
            }

        return result

    def get_all_agent_usage(self) -> Dict:
        """Get usage statistics for all agents."""
        return {
            agent_name: self.get_agent_usage(agent_name)
            for agent_name in self.agent_usage
        }

    def find_agent_instances(self, agent_name: str) -> List[str]:
        """Find all instance IDs for a given agent type."""
        instances = []
        for snapshot_name in self.snapshots.keys():
            if snapshot_name.startswith(f"{agent_name}_") and snapshot_name.endswith(
                "_start"
            ):
                instance_id = snapshot_name[:-6]  # Remove "_start"
                instances.append(instance_id)
        return instances


class AgentSpecificCallback(BaseCallbackHandler):
    def __init__(
        self, agent_name: str, global_callback: BedrockTokenUsageCallbackHandler
    ):
        super().__init__()
        self.agent_name = agent_name
        self.global_callback = global_callback

    def on_llm_start(self, serialized, prompts, **kwargs):
        kwargs["agent_name"] = self.agent_name
        return self.global_callback.on_llm_start(serialized, prompts, **kwargs)

    def on_llm_end(self, response, **kwargs):
        return self.global_callback.on_llm_end(response, **kwargs)

    # Delegate everything else without modification using __getattr__
    def __getattr__(self, name):
        return getattr(self.global_callback, name)
