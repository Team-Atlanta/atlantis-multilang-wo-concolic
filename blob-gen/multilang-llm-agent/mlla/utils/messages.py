from typing import Any, Union

from langchain_core.messages import BaseMessage
from loguru import logger


def add_cache_control(
    message: BaseMessage,
    cache_control: str = "ephemeral",
    cache_ttl: str = "1h",
) -> BaseMessage:
    if message.type not in ["human", "system"]:
        # logger.warning(f"Cache control does not apply to {message.type} -> human")
        message.type = "human"
        # return message

    content = message.content
    new_content: list[Union[str, dict[str, Any]]] = []
    if isinstance(content, str):
        new_content.append(
            {
                "text": content,
                "type": "text",
                "cache_control": {"type": cache_control, "ttl": cache_ttl},
            }
        )
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                new_content.append(
                    {
                        "text": item,
                        "type": "text",
                    }
                )
            elif isinstance(item, dict):
                if "cache_control" in item:
                    logger.warning(
                        f"Cache control already exists: {item}. Skipping adding cache"
                        " control."
                    )
                    return message
                new_content.append(item)

        if len(new_content) > 0 and isinstance(new_content[-1], dict):
            new_content[-1]["cache_control"] = {"type": cache_control, "ttl": cache_ttl}
        else:
            logger.warning(f"Try to convert empty content: {message}")
    else:
        logger.warning(f"Unsupported content type: {type(content)}")
        return message

    message.content = new_content
    return message


def remove_cache_control(message: BaseMessage) -> BaseMessage:
    """Remove cache control from message content, converting back to simple format."""
    content = message.content

    if isinstance(content, list):
        # Process list content to remove cache control
        new_content = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item and "type" in item and item["type"] == "text":
                    # This looks like a cache-controlled item, extract just the text
                    new_content.append(item["text"])
                else:
                    # Keep other dict items but remove cache_control key
                    clean_item = {k: v for k, v in item.items() if k != "cache_control"}
                    new_content.append(clean_item)
            else:
                # Keep non-dict items as-is
                new_content.append(item)

        # Simplify content if possible
        if len(new_content) == 1 and isinstance(new_content[0], str):
            # Single text item - convert back to simple string
            message.content = new_content[0]
        else:
            # Keep as list
            message.content = new_content

    # If content is already a string, no cache control to remove
    return message
