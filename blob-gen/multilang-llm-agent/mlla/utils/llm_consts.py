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
            "model": "openai/gpt-4o",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-7-sonnet-20250219",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-sonnet-20241022",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-haiku-20241022",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash-lite",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-flash",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-flash-8b",
        },
    },
    {
        "model_name": ATLANTA_CHAT,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-pro",
        },
    },
    # atlanta-tool
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gpt-4o",
        },
    },
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-7-sonnet-20250219",
        },
    },
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-sonnet-20241022",
        },
    },
    {
        "model_name": ATLANTA_TOOL,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-haiku-20241022",
        },
    },
    # atlanta-reasoning
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/o1",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/o1-preview",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/o1-mini",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/o3-mini",
        },
    },
    {
        "model_name": ATLANTA_REASONING,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash-thinking-exp",
        },
    },
    {
        "model_name": ATLANTA_CLAUDE,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-sonnet-20241022",
        },
    },
    {
        "model_name": ATLANTA_CLAUDE,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/claude-3-5-haiku-20241022",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-flash",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-flash-8b",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-1.5-pro",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash",
        },
    },
    {
        "model_name": ATLANTA_GEMINI,
        "litellm_params": {  # params for litellm completion/embedding call
            "model": "openai/gemini-2.0-flash-lite",
        },
    },
]
