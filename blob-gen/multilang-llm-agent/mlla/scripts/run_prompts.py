import argparse
import getpass
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from loguru import logger

load_dotenv(".env.secret")


def load_prompts(prompt_file_path):
    """Load prompts from a .prompts file."""
    try:
        with open(prompt_file_path, "r", encoding="utf-8") as f:
            data = f.read()
            fixed = data.replace("\x0a", "")
            prompt_data = json.loads(fixed)
        return prompt_data
    except Exception as e:
        logger.error(f"Error loading prompts from {prompt_file_path}: {e}")
        return None


def convert_to_langchain_messages(prompt_data):
    """Convert JSON prompt format to LangChain messages."""
    messages = []

    for item in prompt_data:
        message_type = item.get("type", "unknown")
        content = item.get("content", "")

        if message_type == "system":
            messages.append(SystemMessage(content=content))
        elif message_type == "human":
            messages.append(HumanMessage(content=content))
        elif message_type == "ai":
            messages.append(AIMessage(content=content))
        else:
            # Default to HumanMessage for unknown types
            messages.append(HumanMessage(content=content))

    return messages


def main():
    parser = argparse.ArgumentParser(
        description="Test ChatOpenAI with prompts from file"
    )
    parser.add_argument("--prompt", required=True, help="Path to .prompts file")
    parser.add_argument("--model", default="gpt-4", help="OpenAI model to use")
    parser.add_argument(
        "--trials", type=int, default=1, help="Number of times to call the LLM"
    )

    args = parser.parse_args()

    # Check if prompt file exists
    prompt_path = Path(args.prompt)
    if not prompt_path.exists():
        logger.error(f"Prompt file {args.prompt} does not exist")
        return

    # Load prompts
    prompt_data = load_prompts(args.prompt)
    if prompt_data is None:
        return

    # Convert to LangChain messages
    messages = convert_to_langchain_messages(prompt_data)
    if not messages:
        logger.error("No valid messages found in prompt file")
        return

    logger.info(f"Loaded {len(messages)} messages from {args.prompt}")
    for i, msg in enumerate(messages):
        logger.info(f"Message {i+1}: {type(msg).__name__}")

    # Setup LLM
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

    llm = ChatOpenAI(
        model=args.model,
        api_key=KEY,
        base_url=URL,
    )

    def _register_tools(llm):
        from langchain_community.agent_toolkits import FileManagementToolkit

        from mlla.utils.llm import PrioritizedTool
        from mlla.utils.llm_tools.astgrep import create_ag_tools
        from mlla.utils.llm_tools.ripgrep import create_rg_tool

        file_system_tools = FileManagementToolkit(
            # root_dir=str(gc.cp.cp_src_path),
            selected_tools=["read_file", "list_directory", "file_search"],
        ).get_tools()

        tools = []
        rg_tool = create_rg_tool(is_dev=False)
        if rg_tool:
            tools.append(rg_tool)

        tools += create_ag_tools() + PrioritizedTool.from_tools(file_system_tools, 1)

        _tools = [tool.get_tool() for tool in tools]
        _tools_dict = {tool.name: tool for tool in _tools}

        llm = llm.bind_tools(_tools)
        return llm, _tools_dict

    # llm, tools_dict = _register_tools(llm)

    # logger.info(f"Tools: {tools_dict}")

    # Invoke LLM multiple times
    total_usage = None
    for trial in range(args.trials):
        try:
            logger.info(
                f"Trial {trial + 1}/{args.trials}: Invoking {args.model} with loaded"
                " prompts..."
            )
            response = llm.invoke(messages)

            logger.info(f"Response {trial + 1}:")
            if response.content:
                logger.info(response.content)
            else:
                logger.info(response)

            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                logger.info(f"Usage metadata for trial {trial + 1}: {usage}")

                # Accumulate total usage
                if total_usage is None:
                    total_usage = dict(usage)
                else:
                    for key, value in usage.items():
                        if isinstance(value, (int, float)):
                            total_usage[key] = total_usage.get(key, 0) + value

            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Error in trial {trial + 1}: {e}")

    # Print total usage if multiple trials
    if args.trials > 1 and total_usage:
        logger.info(f"Total usage across {args.trials} trials: {total_usage}")


if __name__ == "__main__":
    main()
