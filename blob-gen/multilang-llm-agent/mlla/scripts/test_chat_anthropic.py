import getpass
import os

import requests
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

load_dotenv(".env.secret")

KEY = (
    getpass.getpass("Enter your LiteLLM API key: ").strip()
    if os.getenv("LITELLM_KEY") is None
    else os.getenv("LITELLM_KEY")
)

URL = (
    input("Enter your LiteLLM URL: ").strip()
    if os.getenv("LITELLM_URL") is None
    else os.getenv("LITELLM_URL")
)


llm = ChatAnthropic(
    model="claude-3-7-sonnet-20250219",
    api_key=KEY,
    base_url=URL,
)

# Pull LangChain readme
get_response = requests.get(
    "https://raw.githubusercontent.com/langchain-ai/langchain/master/README.md"
)
readme = get_response.text

messages = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are a technology expert.",
            },
            {
                "type": "text",
                "text": f"{readme}",
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "What's LangChain, according to its README?",
                "cache_control": {"type": "ephemeral"},
            },
        ],
    },
]

response_1 = llm.invoke(messages)
response_2 = llm.invoke(messages)

usage_1 = response_1.usage_metadata
usage_2 = response_2.usage_metadata

print(f"First invocation:\n{usage_1}")
print(f"\nSecond:\n{usage_2}")
