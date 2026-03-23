"""Constants for LLM module."""

# Atlanta model constants
ATLANTA_CHAT = "atlanta"
ATLANTA_TOOL = "atlanta-tool"
ATLANTA_REASONING = "atlanta-reasoning"
ATLANTA_CLAUDE = "atlanta-claude"
ATLANTA_GEMINI = "atlanta-gemini"

CUSTOM_MODELS = [
    ATLANTA_CHAT,
    ATLANTA_TOOL,
    ATLANTA_REASONING,
    ATLANTA_CLAUDE,
    ATLANTA_GEMINI,
]

MODEL_LIST: list[dict[str, str | dict[str, str]]] = [
    # atlanta
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-sonnet-4-6",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-haiku-4-5-20251001",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-3.1-pro-preview",
        },
    },
    # atlanta-tool
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4",
        },
    },
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-sonnet-4-6",
        },
    },
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-haiku-4-5-20251001",
        },
    },
    # atlanta-reasoning
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4-mini",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-5.4-mini",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-3.1-pro-preview",
        },
    },
    {
        "model_name": ATLANTA_CLAUDE,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-sonnet-4-6",
        },
    },
    {
        "model_name": ATLANTA_CLAUDE,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-haiku-4-5-20251001",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-3.1-pro-preview",
        },
    },
]
