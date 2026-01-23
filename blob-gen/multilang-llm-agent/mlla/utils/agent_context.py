import contextvars
from typing import Optional

# Context variable for tracking current agent instance
current_agent_instance: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_agent_instance", default=None
)


def set_agent_instance_context(instance_id: str) -> None:
    """Set the current agent instance context."""
    current_agent_instance.set(instance_id)


def get_agent_instance_context() -> Optional[str]:
    """Get the current agent instance context."""
    return current_agent_instance.get()


def get_agent_name_from_instance() -> Optional[str]:
    """Extract agent name from instance ID (e.g., 'cpua_12345' -> 'cpua')."""
    instance_id = current_agent_instance.get()
    if instance_id and "_" in instance_id:
        return "_".join(instance_id.split("_")[:-1])
    return instance_id


def clear_agent_context() -> None:
    """Clear the current agent context."""
    current_agent_instance.set(None)
