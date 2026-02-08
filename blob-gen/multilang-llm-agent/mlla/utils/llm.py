import asyncio
import dataclasses
import inspect
import json
import threading
import time
import traceback
from copy import deepcopy
from functools import partial, reduce
from typing import Any, Callable, Optional, TypeVar

from dateutil import parser
from langchain_anthropic import ChatAnthropic
from langchain_community.chat_models import ChatLiteLLMRouter
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import get_buffer_string
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.tools.base import BaseTool

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from litellm import Router

# from litellm.exceptions import (
#     APIConnectionError,
#     APIError,
#     RateLimitError,
#     ServiceUnavailableError,
#     Timeout,
# )
from loguru import logger
from tenacity import (  # stop_after_attempt,; retry_if_exception_type,
    retry,
    retry_if_exception,
    wait_exponential,
)
from tokencost import TOKEN_COSTS, count_string_tokens

from ..prompts.llm import ASK_AND_REPEAT_UNTIL_MSG, CONTINUE_MSG, TOOL_DESC
from .agent_context import get_agent_instance_context, get_agent_name_from_instance
from .autoprompt import AutoPromptChoice, generate_prompt
from .bedrock_callback import AgentSpecificCallback
from .code_tags import END_TOOLS_TAG, TOOLS_TAG
from .context import GlobalContext
from .llm_consts import (
    ATLANTA_CHAT,
    ATLANTA_REASONING,
    ATLANTA_TOOL,
    CUSTOM_MODELS,
    MODEL_LIST,
)
from .messages import add_cache_control, remove_cache_control

T = TypeVar("T")

UNKNOWN_ERROR_MAX_RETRIES = 10


def is_rate_limit_error(e: Exception) -> bool:
    """Check if an exception is a rate limit error."""
    if "RateLimitError" in str(e) or "rate_limit_error" in str(e) or "429" in str(e):
        return True
    return False


def is_context_limit_error(e: Exception) -> bool:
    """Check if an exception is a context limit error."""
    context_limit_error_indicators = [
        "input length and `max_tokens` exceed context limit",
        "ContextWindowExceededError",
        "exceeds the maximum number of tokens allowed",
        "prompt is too long:",
    ]
    error_str = str(e)
    if any(indicator in error_str for indicator in context_limit_error_indicators):
        return True
    return False


# XXX: This need to be updated to check for the correct error message
def is_unresolvable_error(e: Exception) -> bool:
    """Check if an exception is a quota exceeded error."""
    unresolvable_error_indicators = [
        "generativelanguage.googleapis.com/generate_requests_per_model_per_day",
        "'type': 'insufficient_quota', 'param': None, 'code': 'insufficient_quota'",
        "Your credit balance is too low to access the Anthropic API.",
        "Please go to Plans & Billing to upgrade or purchase credits.",
        "Budget has been exceeded! Current cost",
        "ExceededBudget",
        "ContentPolicyViolationError",
    ]
    error_str = str(e)
    if any(indicator in error_str for indicator in unresolvable_error_indicators):
        logger.error(f"Unresolvable error detected: {error_str}")
        return True
    return False


def is_server_error(e: Exception) -> bool:
    """Check if an exception is a server error (includes rate limit errors)."""
    error_str = str(e)
    server_error_indicators = [
        "RateLimitError",
        "rate_limit_error",
        "429",
        "ServiceUnavailableError",
        "service_unavailable",
        "503",
        "APIError",
        "api_error",
        "500",
        "APIConnectionError",
        "connection_error",
        "Timeout",
        "timeout_error",
        "Connection error",
        "Request timed out",
        "502 Bad Gateway",
        "overloaded_error",
    ]

    if any(indicator in error_str for indicator in server_error_indicators):
        return True
    return False


def _retry_if_exception(e: Exception) -> bool:
    return (
        is_server_error(e)
        and not is_unresolvable_error(e)
        and not is_context_limit_error(e)
    )


def log_retry_error(retry_state):
    """Log retry attempts for better visibility."""
    exception = retry_state.outcome.exception()
    if exception:
        logger.warning(
            f"Retrying LLM call after error: {type(exception).__name__}:"
            f" {str(exception)}. Attempt {retry_state.attempt_number}"
        )
    return True


retry_on_llm_errors = partial(
    retry,
    wait=wait_exponential(multiplier=1, min=4, max=60),
    # stop=stop_after_attempt(10),
    retry=retry_if_exception(_retry_if_exception),
    before_sleep=log_retry_error,
)


def accumulate_content(later_content: str, prev_content: str) -> str:
    splitted_prev_content = prev_content.splitlines()
    splitted_later_content = later_content.splitlines()
    if len(splitted_prev_content) == 0:
        return later_content
    if len(splitted_later_content) == 0:
        return prev_content
    stripped_last_sentence = splitted_prev_content[-1].strip()
    if stripped_last_sentence in later_content:
        index = later_content.index(stripped_last_sentence)
        content = prev_content + later_content[index + len(stripped_last_sentence) :]
        index2 = content.index(stripped_last_sentence)
        logger.warning(f"attached line: {content[index2:].splitlines()[0]}")
        return content
    else:
        return prev_content + later_content


@dataclasses.dataclass
class PrioritizedTool:
    priority: int  # High number means high priority
    _tool: BaseTool

    @staticmethod
    def from_tool(tool: BaseTool, priority: int) -> "PrioritizedTool":
        return PrioritizedTool(priority=priority, _tool=tool)

    @staticmethod
    def from_tools(
        tools: list[BaseTool], priority: list[int] | int
    ) -> list["PrioritizedTool"]:
        if isinstance(priority, int):
            _priority = [priority] * len(tools)
        elif len(tools) != len(priority):
            logger.error("Length of tools and priority should be the same.")
            init_val = 0
            if len(priority) > 0:
                init_val = priority[0]
            _priority = priority + ([init_val] * (len(tools) - len(priority)))

        return [
            PrioritizedTool.from_tool(tool, __priority)
            for tool, __priority in zip(tools, _priority)
        ]

    def get_tool(self) -> BaseTool:
        return self._tool


def get_custom_model_list(model_name: str) -> list[str]:
    res = []

    for model in MODEL_LIST:
        if model["model_name"] == model_name:
            litellm_params = model["litellm_params"]
            if isinstance(litellm_params, dict) and "model" in litellm_params:
                res.append(litellm_params["model"])

    return res


def set_chat_litellm_router(
    config: GlobalContext,
    model: str,
    temperature: int,
    rate_limiter: InMemoryRateLimiter,
) -> ChatLiteLLMRouter:
    import os

    os.environ["OPENAI_API_KEY"] = config.api_key

    router_model_list = []

    for model_entry in MODEL_LIST:
        litellm_params = model_entry["litellm_params"]
        if isinstance(litellm_params, dict):
            litellm_params["api_key"] = config.api_key
            litellm_params["api_base"] = config.base_url
            model_entry["litellm_params"] = litellm_params
            router_model_list.append(model_entry)

    if os.getenv("REDIS_URL"):
        litellm_router = Router(
            model_list=router_model_list,
            routing_strategy="usage-based-routing-v2",
            enable_pre_call_checks=True,
            redis_url=os.getenv("REDIS_URL"),
        )
    else:
        litellm_router = Router(
            model_list=router_model_list,
            routing_strategy="usage-based-routing-v2",
            enable_pre_call_checks=True,
        )

    chat_model = ChatLiteLLMRouter(
        model_name=model,
        temperature=temperature,
        api_base=config.base_url,
        router=litellm_router,
        timeout=config.atlanta_timeout,
        max_retries=config.atlanta_max_retries,
        callbacks=[config.general_callback],
        rate_limiter=rate_limiter,
    )
    return chat_model


def standardize_model_name(model_name: str) -> str:
    model_name = model_name.lower()

    # "openai/oai-gpt-4o" -> "oai-gpt-4o"
    if "/" in model_name:
        model_name = model_name.split("/")[-1]

    # "oai-gpt-4o" -> "gpt-4o"
    # if model_name.startswith("oai-"):
    #     model_name = model_name[4:]

    if model_name in TOKEN_COSTS:
        return model_name

    model_cands: list[tuple[str, str]] = []

    for m_name in TOKEN_COSTS:
        if m_name.startswith(model_name):
            model_cands.append(("", m_name))
        elif m_name.split("/")[-1].startswith(model_name):
            model_cands.append((m_name.split("/")[0] + "/", m_name))

    try:
        # get latest version
        model_name = max(
            model_cands,
            key=lambda m: parser.parse(m[1][len(m[0]) + len(model_name) + 1 :]),
        )[1]
    except Exception:
        if len(model_cands) != 0:
            model_name = model_cands[0][1]

    return model_name


def get_large_context_model_name(model: str) -> tuple[str, Optional[str]]:
    if "gemini-2.5-pro" in model:
        return "gpt-4.1", None
    elif "sonnet" in model or "opus" in model:
        return "gemini-2.5-pro", "gpt-4.1"
    elif "o4-mini" in model:
        return "gemini-2.5-pro", "gpt-4.1"
    elif "haiku" in model:
        return "gpt-4.1", "gemini-2.5-flash"
    else:
        return "gpt-4.1", "gemini-2.5-flash"


def get_rate_limit_fallback_model_name(model: str) -> Optional[str]:
    # lets do sonnet-4 <-> opus-4
    # else, we will use o3
    if "claude-sonnet-4" in model:
        return "claude-opus-4-20250514"
    elif "claude-opus-4" in model:
        return "claude-sonnet-4-20250514"
    else:
        return "o3"
    # lets focus on
    # elif "o3" in model:
    #     return "gemini-2.5-pro"
    # else:
    #     return "gpt-4.1"


class LLM:
    chat_model: BaseChatModel
    model_name: str
    tools: list[PrioritizedTool]
    response_format: Optional[Any] = None
    summarize_chat_model: BaseChatModel
    prompt_caching: bool = False  # This only works for ChatAnthropic
    agent_name: str = ""
    large_context_model: Optional["LLM"] = None
    large_context_model_fallback: Optional["LLM"] = None

    def __init__(
        self,
        model: str,
        config: GlobalContext,
        tools: list[PrioritizedTool] = [],
        output_format=None,
        temperature=0,
        max_tokens: Optional[int] = None,
        concurrent_limit: Optional[int] = None,
        prompt_caching: bool = False,
        agent_name: Optional[str] = None,
        prepare_large_context_model: bool = True,
    ):

        # Use global rate limiters from config
        rate_limiter = config.global_rate_limiter
        claude_rate_limiter = config.global_claude_rate_limiter

        # Handle agent name: explicit takes precedence over context
        if agent_name:
            # Explicit agent name provided - use it directly
            self.agent_name = agent_name
            self.instance_id = (
                get_agent_instance_context()
            )  # May be None if no context set
        else:
            # Try to auto-detect from context
            context_agent_name = get_agent_name_from_instance()
            if context_agent_name:
                self.agent_name = context_agent_name
                self.instance_id = get_agent_instance_context()
            else:
                # No agent name provided and no context - use empty
                self.agent_name = ""
                self.instance_id = None

        if self.agent_name:
            self.callbacks = [
                AgentSpecificCallback(self.agent_name, config.general_callback)
            ]
        else:
            self.callbacks = [config.general_callback]

        if model in [ATLANTA_CHAT, ATLANTA_TOOL, ATLANTA_REASONING]:
            chat_model = set_chat_litellm_router(
                config, model, temperature, rate_limiter
            )
        elif "gemini" in model:
            chat_model = ChatOpenAI(
                model=model,
                temperature=temperature,
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.gemini_timeout,
                max_retries=config.gemini_max_retries,
                max_tokens=max_tokens,
                callbacks=self.callbacks,
                rate_limiter=claude_rate_limiter,
            )

        elif "claude" in model:
            model_data = TOKEN_COSTS.get(model, {})
            _max_tokens = model_data.get("max_output_tokens", 8192)
            if max_tokens:
                max_tokens = min(_max_tokens, max_tokens)
            else:
                max_tokens = _max_tokens
            logger.info(f"Initializing {model} (max_tokens: {max_tokens})")

            chat_model = ChatAnthropic(
                model=model,
                temperature=temperature,
                api_key=config.api_key,
                base_url=config.base_url,
                # tiktoken_model_name="claude-3-7-sonnet-20250219",
                timeout=config.openai_timeout,
                max_retries=config.openai_max_retries,
                max_tokens=max_tokens,
                callbacks=self.callbacks,
                rate_limiter=claude_rate_limiter,
                betas=["extended-cache-ttl-2025-04-11"],
            )

            # self.token_count_client = Anthropic(
            #     api_key=config.api_key, base_url=config.base_url + "/anthropic"
            # )

        else:
            temperature = temperature
            if model.startswith("o"):
                temperature = 1

            chat_model = ChatOpenAI(
                model=model,
                temperature=temperature,
                api_key=config.api_key,
                base_url=config.base_url,
                tiktoken_model_name="gpt-4o",
                timeout=config.openai_timeout,
                max_retries=config.openai_max_retries,
                max_tokens=max_tokens,
                callbacks=self.callbacks,
                rate_limiter=rate_limiter,
            )

        self.chat_model = chat_model
        if prepare_large_context_model:
            large_context_model_name, large_context_model_fallback_name = (
                get_large_context_model_name(model)
            )
            model_data = TOKEN_COSTS.get(large_context_model_name, None)
            if max_tokens is not None and model_data is not None:
                _max_tokens = model_data.get("max_output_tokens", 4096)
                _max_tokens = min(max_tokens, _max_tokens)
            else:
                _max_tokens = max_tokens

            self.large_context_model = LLM(
                model=large_context_model_name,
                config=config,
                tools=tools,
                output_format=output_format,
                temperature=0,
                max_tokens=_max_tokens,
                concurrent_limit=concurrent_limit,
                # prompt_caching=prompt_caching, # this cannot be a claude model
                agent_name=agent_name,
                prepare_large_context_model=False,
            )
            if large_context_model_fallback_name is not None:
                model_data = TOKEN_COSTS.get(large_context_model_fallback_name, None)
                if max_tokens is not None and model_data is not None:
                    _max_tokens = model_data.get("max_output_tokens", 4096)
                    _max_tokens = min(max_tokens, _max_tokens)
                else:
                    _max_tokens = max_tokens

                self.large_context_model_fallback = LLM(
                    model=large_context_model_fallback_name,
                    config=config,
                    tools=tools,
                    output_format=output_format,
                    temperature=0,
                    max_tokens=_max_tokens,
                    concurrent_limit=concurrent_limit,
                    # prompt_caching=prompt_caching, # this cannot be a claude model
                    agent_name=agent_name,
                    prepare_large_context_model=False,
                )
            else:
                self.large_context_model_fallback = None
        # assert (
        #     len(tools) == 0 or output_format is None
        # ), "Only one of tools or output_format should be provided."

        if len(tools) > 0:
            _tools = [tool.get_tool() for tool in tools]
            chat_model = chat_model.bind_tools(_tools)

        if output_format is not None:
            if model.startswith("o"):
                # o* models don't support structured output as a tool calling
                self.response_format = output_format
            else:
                chat_model = chat_model.with_structured_output(output_format)

        self.runnable_chat_model = chat_model
        self.model_name = model
        self.gc = config
        self.tools = tools
        self.summarize_chat_model = ChatOpenAI(
            model="gpt-4.1-mini",
            temperature=0,
            api_key=config.api_key,
            base_url=config.base_url,
            tiktoken_model_name="gpt-4o",
            timeout=config.openai_timeout,
            max_retries=config.openai_max_retries,
            callbacks=self.callbacks,
            rate_limiter=rate_limiter,
        )
        self.prompt_caching = prompt_caching  # This only works for ChatAnthropic

    def get_context_limit(self) -> int:
        """Returns the token limit for the given model name."""
        # TODO: max token limit for model with tools should be different
        model_data = TOKEN_COSTS.get(self.model_name, None)
        if model_data is None:
            if self.model_name in CUSTOM_MODELS:
                custom_model_list = get_custom_model_list(self.model_name)
                min_max_token = min(
                    [
                        TOKEN_COSTS.get(standardize_model_name(m), {}).get(
                            "max_input_tokens", 8192
                        )
                        for m in custom_model_list
                    ]
                )
                if min_max_token == 8192:
                    logger.warning(f"Model {self.model_name}: {min_max_token}")
                    logger.warning(f"Model {self.model_name}: {custom_model_list}")
                    for m in custom_model_list:
                        logger.warning(f"Model {m}: {standardize_model_name(m)}")
                        logger.warning(
                            f"model info: {TOKEN_COSTS.get(standardize_model_name(m))}"
                        )
                context_limit = min_max_token
            else:
                logger.warning(f"Model {self.model_name} not found in TOKEN_COSTS")
                context_limit = 8192
        else:
            context_limit = model_data.get("max_input_tokens", 8192)

        conservative_buffer = 1000
        reserved_for_output = (
            0
            if not hasattr(self.runnable_chat_model, "max_tokens")
            or self.runnable_chat_model.max_tokens is None
            else self.runnable_chat_model.max_tokens
        )
        input_limit = max(context_limit - reserved_for_output - conservative_buffer, 0)
        if input_limit == 0:
            logger.error(
                f"Model {self.model_name} has no input limit. check the configuration"
            )
        return input_limit

    def get_output_limit(self) -> int:
        """Returns the output token limit for the given model name."""
        model_data = TOKEN_COSTS.get(self.model_name, None)
        if model_data is None:
            if self.model_name in CUSTOM_MODELS:
                min_max_token = min(
                    [
                        TOKEN_COSTS.get(standardize_model_name(m), {}).get(
                            "max_output_tokens", 4096
                        )
                        for m in get_custom_model_list(self.model_name)
                    ]
                )
                return min_max_token
            else:
                logger.error(f"Model {self.model_name} not found in TOKEN_COSTS")
                return 4096
        return model_data.get("max_output_tokens", 4096)

    def _create_fallback_llm(
        self, fallback_model_name: str, prepare_large_context_model: bool = True
    ) -> "LLM":
        """Create a fallback LLM instance with the same settings but different model."""
        # Get model-specific max_tokens
        model_data = TOKEN_COSTS.get(fallback_model_name, {})
        fallback_max_tokens = model_data.get("max_output_tokens", 4096)

        # Get current temperature from runnable model
        current_temperature = getattr(self.runnable_chat_model, "temperature", 0)

        if fallback_model_name.startswith("o"):
            current_temperature = 1.0

        # Create with same settings but different model
        return LLM(
            model=fallback_model_name,
            config=self.gc,
            tools=self.tools,
            output_format=self.response_format,
            temperature=current_temperature,
            max_tokens=fallback_max_tokens,
            agent_name=self.agent_name,
            prepare_large_context_model=prepare_large_context_model,
            # Don't create nested fallbacks as default
        )

    def _invoke_model_with_retry(
        self,
        messages: list[BaseMessage],
        max_retries: int = 5,
        second_chance: bool = False,
        **kwargs,
    ) -> BaseMessage:
        """Invoke the model with retry logic for handling exceptions."""
        retry_attempt = 0

        while True:
            try:
                if self.response_format is not None:
                    return self.runnable_chat_model.invoke(
                        messages, response_format=self.response_format, **kwargs
                    )
                else:
                    return self.runnable_chat_model.invoke(messages, **kwargs)

            except Exception as e:
                retry_attempt += 1

                # Log debug info if in dev mode
                if self.gc.is_dev:
                    for idx, msg in enumerate(messages):
                        if isinstance(msg.content, dict):
                            content = msg.content["text"]
                        else:
                            content = msg.content
                        logger.debug(
                            f"- [{idx}] {msg.type} ({len(content)}): {content[:100]}"
                        )
                    logger.error(f"Error occurred: {e}")

                # Check if this is a non-retryable error
                if is_context_limit_error(e) or is_unresolvable_error(e):
                    # This will trigger context limit fallback or exit
                    raise e

                # First, wait with exponential backoff
                wait_time = min(4 * (2**retry_attempt), 60)
                logger.warning(
                    f"Retrying LLM call after error: {type(e).__name__}: {str(e)}. "
                    f"Attempt {retry_attempt}, waiting {wait_time}s"
                )

                # Check if we're in an async context and use appropriate sleep
                try:
                    # Try to get the current event loop
                    loop = asyncio.get_running_loop()
                    # If we're in an async context, we need a different approach
                    # Since this is a sync method, we can't use await here
                    # Instead, we'll use a thread-safe approach

                    event = threading.Event()

                    def wake_up():
                        event.set()

                    # Schedule the wake up in the event loop
                    loop.call_later(wait_time, wake_up)
                    # Wait for the event (this doesn't block the event loop)
                    event.wait()
                except RuntimeError:
                    # No event loop running, safe to use time.sleep
                    time.sleep(wait_time)

                if is_rate_limit_error(e):
                    if retry_attempt > max_retries:
                        # Check for second chance
                        if second_chance:
                            logger.info(
                                f"Second chance retrial for {self.model_name} after"
                                f" {retry_attempt} retries."
                            )
                            return self._invoke_model_with_retry(
                                messages,
                                max_retries=max_retries,
                                second_chance=False,
                                **kwargs,
                            )
                        else:
                            # This will trigger rate limit fallback
                            raise e

                    logger.error(
                        f"RateLimit error: {e}. Retrying {retry_attempt}/{max_retries}"
                    )
                    continue

                if is_server_error(e):
                    logger.error(f"Server error: {e}. {retry_attempt} attempts.")
                    continue

                logger.error(f"Unknown error: {e}")
                tb_lines = traceback.format_exc()
                logger.warning(f"Traceback: {tb_lines}")

                if retry_attempt < UNKNOWN_ERROR_MAX_RETRIES:
                    logger.warning(
                        f"Unknown Retrying: {retry_attempt}/{UNKNOWN_ERROR_MAX_RETRIES}"
                    )
                    continue

                else:
                    return AIMessage(content="LLM failed to generate a response.")
                    # raise e

        # This should never be reached, but just in case
        return AIMessage(content="LLM failed to generate a response.")

    async def _aclose_clients(self) -> None:
        """Close both sync and async clients."""
        try:
            if hasattr(self.runnable_chat_model.client, "close"):
                self.runnable_chat_model.client.close()
            if hasattr(self.runnable_chat_model.async_client, "aclose"):
                await self.runnable_chat_model.async_client.aclose()
        except Exception as e:
            logger.error(f"Error closing clients: {e}")

    async def _ainvoke_model_with_retry(
        self,
        messages: list[BaseMessage],
        max_retries: int = 5,
        second_chance: bool = False,
        **kwargs,
    ) -> BaseMessage:
        """Invoke the model with retry logic for handling exceptions."""

        retry_attempt = 0

        while True:
            try:
                if self.response_format is not None:
                    response = await self.runnable_chat_model.ainvoke(
                        messages, response_format=self.response_format, **kwargs
                    )
                else:
                    response = await self.runnable_chat_model.ainvoke(
                        messages, **kwargs
                    )

                return response

            except Exception as e:
                retry_attempt += 1

                # Log debug info if in dev mode
                if self.gc.is_dev:
                    for idx, msg in enumerate(messages):
                        if isinstance(msg.content, dict):
                            content = msg.content["text"]
                        else:
                            content = msg.content
                        logger.debug(
                            f"- [{idx}] {msg.type} ({len(content)}): {content[:100]}"
                        )
                    logger.error(f"Error occurred: {e}")

                # Check if this is a non-retryable error
                if is_context_limit_error(e) or is_unresolvable_error(e):
                    # This will trigger context limit fallback or exit
                    raise e

                # First, wait with exponential backoff
                wait_time = min(4 * (2**retry_attempt), 60)
                logger.warning(
                    f"Retrying LLM call after error: {type(e).__name__}: {str(e)}. "
                    f"Attempt {retry_attempt}, waiting {wait_time}s"
                )
                await asyncio.sleep(wait_time)

                if is_rate_limit_error(e):
                    if retry_attempt > max_retries:
                        # Check for second chance
                        if second_chance:
                            logger.info(
                                f"Second chance retrial for {self.model_name} after"
                                f" {retry_attempt} retries."
                            )
                            return await self._ainvoke_model_with_retry(
                                messages,
                                max_retries=max_retries,
                                second_chance=False,
                                **kwargs,
                            )
                        else:
                            # This will trigger rate limit fallback
                            raise e
                    logger.error(
                        f"RateLimit error: {e}. Retrying {retry_attempt}/{max_retries}"
                    )
                    continue

                if is_server_error(e):
                    logger.error(f"Server error: {e}. {retry_attempt} attempts.")
                    continue

                logger.error(f"Unknown error: {e}")
                tb_lines = traceback.format_exc()
                logger.warning(f"Traceback: {tb_lines}")

                if retry_attempt < UNKNOWN_ERROR_MAX_RETRIES:
                    logger.warning(
                        f"Retrying: {retry_attempt}/{UNKNOWN_ERROR_MAX_RETRIES}"
                    )
                    continue

                else:
                    return AIMessage(content="LLM failed to generate a response.")
                    # raise e

        # This should never be reached, but just in case
        return AIMessage(content="LLM failed to generate a response.")

    def _invoke(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice],
        max_retries: int = 5,
        **kwargs,
    ) -> list[BaseMessage]:
        """Core invoke method with support for different prompt choices."""
        idx = 0
        prev_responses = []
        while idx < 10:
            response = self._invoke_model_with_retry(
                messages, max_retries=max_retries, **kwargs
            )
            messages.append(response)

            if isinstance(response, AIMessage) and response.tool_calls:
                return messages

            if (
                isinstance(response, AIMessage)
                and response.response_metadata.get("finish_reason") == "length"
            ):
                prev_responses.append(response)
                messages.append(HumanMessage(CONTINUE_MSG))
                idx += 1
                continue
            else:
                break
        last_message = messages[-1]
        if len(prev_responses) > 0 and isinstance(last_message, AIMessage):
            acc_content = ""
            for prev_response in prev_responses:
                acc_content = accumulate_content(prev_response.content, acc_content)
            messages[-1].content = acc_content + last_message.content
        return messages

    async def _ainvoke(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice],
        max_retries: int = 5,
        **kwargs,
    ) -> list[BaseMessage]:
        """Core invoke method with support for different prompt choices."""
        idx = 0
        prev_responses = []
        while idx < 10:
            response = await self._ainvoke_model_with_retry(
                messages, max_retries=max_retries, **kwargs
            )
            messages.append(response)

            if isinstance(response, AIMessage) and response.tool_calls:
                return messages

            if (
                isinstance(response, AIMessage)
                and response.response_metadata.get("finish_reason") == "length"
            ):
                prev_responses.append(response)
                messages.append(HumanMessage(CONTINUE_MSG))
                idx += 1
                continue
            else:
                break
        last_message = messages[-1]
        if len(prev_responses) > 0 and isinstance(last_message, AIMessage):
            acc_content = ""
            for prev_response in prev_responses:
                acc_content = accumulate_content(prev_response.content, acc_content)
            messages[-1].content = acc_content + last_message.content
        return messages

    def _add_tools_desc(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Add tool descriptions to the system message if needed."""
        if TOOLS_TAG in messages[0].content:
            return messages

        tool_desc = TOOL_DESC
        tools = sorted(self.tools, key=lambda x: x.priority, reverse=True)
        tool_desc += TOOLS_TAG + "\n"
        for tool in tools:
            tool_desc += (
                f"{tool.get_tool().name} (priority: {tool.priority}):"
                f" {tool.get_tool().description}\n"
            )
        tool_desc += END_TOOLS_TAG + "\n"

        messages[0].content += tool_desc
        return messages

    def first_human_msg_to_system_msg(
        self, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        """Convert the first human message to a system message if needed."""
        if len(messages) == 0:
            return messages

        first_message = messages[0]
        if isinstance(first_message, HumanMessage):
            new_message = SystemMessage(
                content=first_message.content, id=first_message.id
            )
            messages[0] = new_message

        return messages

    def _prepare_messages(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice],
        cache: Optional[bool] = None,
    ) -> tuple[list[BaseMessage], list[RemoveMessage]]:
        """Prepare messages for model invocation by applying necessary
        transformations."""
        # Add tool descriptions if needed
        if len(self.tools) > 0:
            first_priority = self.tools[0].priority
            # If all tools have the same priority, we don't need to add tool
            # description
            if not reduce(
                lambda acc, ftool: acc and ftool.priority == first_priority,
                self.tools,
                True,
            ):
                messages = self._add_tools_desc(messages)

        # Convert first human message to system message if needed
        messages = self.first_human_msg_to_system_msg(messages)

        # Generate prompt based on choice
        messages = generate_prompt(messages, self.gc, choice)

        # Summarize messages if needed
        messages = self.summarize(messages)

        remove_messages = [m for m in messages if isinstance(m, RemoveMessage)]
        if len(remove_messages) > 0:
            logger.debug(f"len(remove_messages): {len(remove_messages)}")

        # Remove RemoveMessage from messages
        messages = [m for m in messages if not isinstance(m, RemoveMessage)]

        # Clean up message content
        for m in messages:
            if isinstance(m, BaseMessage):
                if (
                    isinstance(m.content, list)
                    and len(m.content) > 0
                    and isinstance(m.content[0], str)
                ):
                    m.content = [inspect.cleandoc(c) for c in m.content]
                elif isinstance(m.content, str):
                    m.content = inspect.cleandoc(m.content)

        if isinstance(self.chat_model, ChatAnthropic):
            if cache is not None:
                logger.debug(f"Cache value is explicitly given: {cache}")

            if cache or (self.prompt_caching and cache is None):
                add_cache_control(messages[-1])

        return messages, remove_messages

    @retry_on_llm_errors()
    def invoke_large_model(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice] = AutoPromptChoice.NOCHANGE,
        cache: Optional[bool] = None,
        **kwargs,
    ) -> list[BaseMessage]:
        if self.large_context_model is None:
            logger.error(
                "Large context model is not set, but invoke_large_model is called"
            )
            raise ValueError(
                "Large context model is not set, but invoke_large_model is called"
            )

        # Create clean copies of messages without cache control
        clean_messages = deepcopy(messages)
        for message in clean_messages:
            remove_cache_control(message)

        logger.debug("Removed cache control from messages for large model")

        try:
            result = self.large_context_model.invoke(
                messages=clean_messages, choice=choice, cache=False, **kwargs
            )
        except Exception as e:
            if (
                is_unresolvable_error(e) or is_context_limit_error(e)
            ) and self.large_context_model_fallback is not None:
                logger.error(f"First large context model failed: {str(e)}")
                result = self.large_context_model_fallback.invoke(
                    messages=clean_messages, choice=choice, cache=False, **kwargs
                )
            else:
                raise e
        return result

    @retry_on_llm_errors()
    async def ainvoke_large_model(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice] = AutoPromptChoice.NOCHANGE,
        cache: Optional[bool] = None,
        **kwargs,
    ) -> list[BaseMessage]:
        if self.large_context_model is None:
            logger.error(
                "Large context model is not set, but invoke_large_model is called"
            )
            raise ValueError(
                "Large context model is not set, but invoke_large_model is called"
            )

        # Create clean copies of messages without cache control
        clean_messages = deepcopy(messages)
        for message in clean_messages:
            remove_cache_control(message)

        logger.debug("Removed cache control from messages for large model")

        try:
            result = await self.large_context_model.ainvoke(
                messages=clean_messages, choice=choice, cache=False, **kwargs
            )
        except Exception as e:
            if (
                is_unresolvable_error(e) or is_context_limit_error(e)
            ) and self.large_context_model_fallback is not None:
                logger.error(f"First large context model failed: {str(e)}")
                result = await self.large_context_model_fallback.ainvoke(
                    messages=clean_messages, choice=choice, cache=False, **kwargs
                )
            else:
                raise e
        return result

    def invoke(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice] = AutoPromptChoice.NOCHANGE,
        cache: Optional[bool] = None,
        cache_index: int = 0,
        force_large_model: bool = False,
        large_model_callback: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> list[BaseMessage]:
        """Invoke the model with the given messages."""
        # Validate that messages is not empty
        if not messages:
            logger.error("Empty messages list passed to LLM.invoke")
            # just return an empty message?
            # raise ValueError("Cannot invoke LLM with empty messages list")
            return [AIMessage(content="")]

        cur_model_name = self.model_name

        # Create a deep copy to avoid modifying the original messages list
        messages_copy = deepcopy(messages)

        # If the last message is an AI message with tool calls, don't modify the prompt
        response = messages_copy[-1]
        if isinstance(response, AIMessage) and response.tool_calls:
            choice = AutoPromptChoice.NOCHANGE

        # Prepare messages for invocation
        prepared_messages, remove_messages = self._prepare_messages(
            messages_copy,
            choice,
            cache,
        )

        # If force_large_model is True, directly use large model
        if force_large_model and self.large_context_model is not None:
            return self.invoke_large_model(
                messages=messages, choice=choice, cache=False, **kwargs
            )

        if cache:
            # Lets caching only for claude
            if self.model_name.startswith("claude"):
                add_cache_control(prepared_messages[0])
                add_cache_control(prepared_messages[cache_index])

        if self.model_name.startswith("claude-opus-4"):
            max_retries = 1
        else:
            max_retries = 5

        # Determine second_chance based on model
        second_chance = "claude-sonnet-4" in self.model_name

        try:
            # Invoke the model
            responses = self._invoke(
                prepared_messages,
                choice,
                max_retries=max_retries,
                second_chance=second_chance,
                **kwargs,
            )
        except Exception as e:
            if is_context_limit_error(e) and self.large_context_model is not None:
                logger.info(
                    "Context limit error detected. Invoking large context model:"
                    f" {self.large_context_model.model_name}"
                )
                # Call the callback to signal large model usage
                if large_model_callback:
                    large_model_callback()
                return self.invoke_large_model(
                    messages=messages, choice=choice, cache=False, **kwargs
                )
            elif is_rate_limit_error(e):
                # Rate limit fallback after retries are exhausted
                fallback_model_name = get_rate_limit_fallback_model_name(cur_model_name)
                if fallback_model_name:
                    cur_model_name = fallback_model_name
                    logger.info(
                        f"Rate limit on {self.model_name}, trying fallback:"
                        f" {fallback_model_name}"
                    )
                    fallback_llm = self._create_fallback_llm(
                        fallback_model_name, prepare_large_context_model=True
                    )
                    return fallback_llm.invoke(
                        messages=messages,
                        choice=choice,
                        cache=cache,
                        cache_index=cache_index,
                        **kwargs,
                    )
                else:
                    logger.error(
                        f"Rate limit error on {self.model_name} with no fallback"
                        " available"
                    )
                    raise e
            else:
                raise e

        # Return responses with any RemoveMessages
        return responses[:-1] + remove_messages + [responses[-1]]

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        choice: AutoPromptChoice | list[AutoPromptChoice] = AutoPromptChoice.NOCHANGE,
        cache: Optional[bool] = None,
        cache_index: int = 0,
        force_large_model: bool = False,
        large_model_callback: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> list[BaseMessage]:
        """Invoke the model with the given messages asynchronously."""
        # Validate that messages is not empty
        if not messages:
            logger.error("Empty messages list passed to LLM.ainvoke")
            # just return an empty message?
            # raise ValueError("Cannot invoke LLM with empty messages list")
            return [AIMessage(content="")]

        cur_model_name = self.model_name

        # Create a deep copy to avoid modifying the original messages list
        messages_copy = deepcopy(messages)

        # If the last message is an AI message with tool calls, don't modify the prompt
        response = messages_copy[-1]
        if isinstance(response, AIMessage) and response.tool_calls:
            choice = AutoPromptChoice.NOCHANGE

        # Prepare messages for invocation
        prepared_messages, remove_messages = self._prepare_messages(
            messages_copy, choice, cache
        )

        # If force_large_model is True, directly use large model
        if force_large_model and self.large_context_model is not None:
            return await self.ainvoke_large_model(
                messages=messages, choice=choice, cache=False, **kwargs
            )

        if cache:
            # Lets caching only for claude
            if self.model_name.startswith("claude"):
                add_cache_control(prepared_messages[0])
                add_cache_control(prepared_messages[cache_index])

        if self.model_name.startswith("claude-opus-4"):
            max_retries = 1
        else:
            max_retries = 5

        # Determine second_chance based on model
        second_chance = "claude-sonnet-4" in self.model_name

        try:
            # Invoke the model
            responses = await self._ainvoke(
                prepared_messages,
                choice,
                max_retries=max_retries,
                second_chance=second_chance,
                **kwargs,
            )
        except Exception as e:
            if is_context_limit_error(e) and self.large_context_model is not None:
                logger.info(
                    "Context limit error detected. Invoking large context model:"
                    f" {self.large_context_model.model_name}"
                )
                # Call the callback to signal large model usage
                if large_model_callback:
                    large_model_callback()
                return await self.ainvoke_large_model(
                    messages=messages, choice=choice, cache=False, **kwargs
                )
            elif is_rate_limit_error(e):
                # Rate limit fallback after retries are exhausted
                fallback_model_name = get_rate_limit_fallback_model_name(cur_model_name)
                if fallback_model_name:
                    cur_model_name = fallback_model_name
                    logger.info(
                        f"Rate limit on {self.model_name}, trying fallback:"
                        f" {fallback_model_name}"
                    )
                    fallback_llm = self._create_fallback_llm(
                        fallback_model_name, prepare_large_context_model=True
                    )
                    return await fallback_llm.ainvoke(
                        messages=messages,
                        choice=choice,
                        cache=cache,
                        cache_index=cache_index,
                        **kwargs,
                    )
                else:
                    logger.error(
                        f"Rate limit error on {self.model_name} with no fallback"
                        " available"
                    )
                    raise e
            else:
                raise e

        last_message = responses[-1]

        # Return responses with any RemoveMessages
        return responses[:-1] + remove_messages + [last_message]

    def tokenize(self, messages: list[BaseMessage]) -> list[tuple[int, BaseMessage]]:
        """Count tokens for each message in the list."""
        token_cnts = []
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                usage_metadata = msg.usage_metadata
                token_cnt = usage_metadata["total_tokens"]
                token_cnts.append((token_cnt, msg))
            elif isinstance(msg, RemoveMessage):
                token_cnts.append((0, msg))
            else:
                msg_str = get_buffer_string([msg])
                for tool in self.tools:
                    tool_json = tool.get_tool().to_json()
                    tool_json_str = json.dumps(
                        tool_json, separators=(",", ":"), sort_keys=True
                    )
                    msg_str += tool_json_str

                token_cnt = count_string_tokens(msg_str, "gpt-4o")
                # if self.model_name.startswith("claude"):
                #     claude_coeff = 2
                #     token_cnt = int(token_cnt * claude_coeff)
                token_cnts.append((token_cnt, msg))
        return token_cnts

    def _process_message_for_token_limit(
        self, message: BaseMessage, token_cnt: int, max_token_limits: int
    ) -> BaseMessage:
        """Process a message to ensure it doesn't exceed token limits."""
        if isinstance(message, AIMessage) and message.tool_calls:
            return RemoveMessage(id=message.id)
        elif isinstance(message, ToolMessage):
            logger.debug(f"Tool message ({token_cnt}): {message.content[:40]}")
            if token_cnt >= max_token_limits:
                logger.warning(
                    "Tool message exceeds token limit. It's not"
                    + " recommended. Check the tool. This message will be removed."
                )
                return RemoveMessage(id=message.id)
            else:
                return AIMessage(content=message.content, id=message.id)
        elif token_cnt > max_token_limits:
            new_message = deepcopy(message)
            new_message.content = message.content[: int(max_token_limits / 2)]
            return new_message
        else:
            return message

    def polish_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Process messages to ensure they don't exceed token limits."""
        token_cnts = self.tokenize(messages)
        max_token_limits = self.get_context_limit()
        new_messages = []

        for idx, message in enumerate(messages):
            token_cnt = token_cnts[idx][0]
            new_message = self._process_message_for_token_limit(
                message, token_cnt, max_token_limits
            )
            new_messages.append(new_message)

        return new_messages

    def replace_tool_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Replace ToolMessages with AIMessages."""
        new_messages = []
        for message in messages:
            if isinstance(message, ToolMessage):
                new_message = AIMessage(content=message.content)
                new_messages.append(new_message)
        return new_messages

    def _merge_consecutive_messages(
        self, messages: list[BaseMessage]
    ) -> tuple[list[BaseMessage], list[RemoveMessage], int]:
        """Merge consecutive messages of the same type."""
        old_len = len(messages)
        new_messages = [messages[0]]
        remove_messages = []

        for idx in range(1, len(messages), 2):
            message = messages[idx]
            next_message = messages[idx + 1] if idx + 1 < len(messages) else None

            if next_message is None:
                new_messages.append(message)
                break
            if old_len < 2040:
                new_messages.append(message)
                new_messages.append(next_message)
                continue
            elif message.type == next_message.type:
                new_content = message.content + next_message.content
                message.content = new_content
                new_messages.append(message)
                remove_messages.append(RemoveMessage(id=next_message.id))
                old_len -= 1
            else:
                new_messages.append(message)
                new_messages.append(next_message)

        return new_messages, remove_messages, old_len

    def merge_messages(self, messages: list[BaseMessage], it=1) -> list[BaseMessage]:
        """Merge messages to reduce the total number of messages."""
        old_len = len(messages)
        new_messages, remove_messages, new_len = self._merge_consecutive_messages(
            messages
        )

        logger.debug(
            f"len new_messages: {len(new_messages)}, old_len: {old_len}, new_len:"
            f" {new_len}"
        )

        if len(new_messages) <= 2048:
            return new_messages + remove_messages
        else:
            if it > 10:
                return new_messages[0:1] + new_messages[-2040:] + remove_messages
            else:
                return self.merge_messages(new_messages, it=it + 1) + remove_messages

    def summarize(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Summarize messages to fit within token limits."""
        # Handle large number of messages
        messages = self._handle_large_message_count(messages)

        # Skip summarization if large context model is set
        if self.large_context_model is not None:
            return messages

        # Check if we're within token limits
        token_cnts, max_token_limits, n_tokens = self._calculate_token_counts(messages)
        if n_tokens <= max_token_limits:
            return messages

        # Log token information
        self._log_token_info(n_tokens, max_token_limits, len(messages))

        # Handle first message exceeding token limit
        messages = self._handle_first_message_token_limit(
            messages, token_cnts, max_token_limits
        )

        # Polish messages to handle token limits
        messages = self._process_messages_for_token_limits(messages, max_token_limits)

        return messages

    def _handle_large_message_count(
        self, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        """Handle cases where there are too many messages."""
        if len(messages) > 2048:
            logger.debug(f"Number of messages: {len(messages)}")
            messages = self.merge_messages(messages)
            logger.debug(f"Number of messages after merge: {len(messages)}")
        return messages

    def _calculate_token_counts(self, messages: list[BaseMessage]) -> tuple:
        """Calculate token counts and limits for messages."""
        token_cnts = self.tokenize(messages)
        max_token_limits = self.get_context_limit()
        n_tokens = sum([token_cnt for token_cnt, _ in token_cnts])
        return token_cnts, max_token_limits, n_tokens

    def _log_token_info(self, n_tokens: int, max_token_limits: int, message_count: int):
        """Log information about token counts and limits."""
        logger.info(f"n_tokens: {n_tokens}, max_token_limits: {max_token_limits}")
        logger.info(f"Entering summarize w/ {message_count} messages:")

    def _handle_first_message_token_limit(
        self, messages: list[BaseMessage], token_cnts: list, max_token_limits: int
    ) -> list[BaseMessage]:
        """Handle cases where the first message exceeds token limits."""
        if token_cnts[0][0] > max_token_limits:
            logger.error("First message exceeds token limit. Truncating it.")
            messages[0].content = messages[0].content[: int(max_token_limits / 2)]
        return messages

    def _process_messages_for_token_limits(
        self, messages: list[BaseMessage], max_token_limits: int
    ) -> list[BaseMessage]:
        """Process messages to fit within token limits."""
        # Polish messages to handle token limits
        messages = self.polish_messages(messages)
        token_cnts = self.tokenize(messages)
        n_tokens = sum([token_cnt for token_cnt, _ in token_cnts])

        if n_tokens <= max_token_limits:
            return messages

        # Split messages into sections for processing
        first, head, tail, split_idx = self._split_messages_for_processing(
            messages, token_cnts, max_token_limits
        )

        # Process the head section
        summarized_head = self._process_head_section(head, max_token_limits)

        # Combine sections and check if further summarization is needed
        messages = self._combine_message_sections(
            first, summarized_head, tail, max_token_limits
        )

        return messages

    def _split_messages_for_processing(
        self, messages: list[BaseMessage], token_cnts: list, max_token_limits: int
    ) -> tuple:
        """Split messages into sections for processing."""
        tail_tokens = 0
        split_idx = len(messages)
        pivot = 1  # Keeps first and last message from summary

        possible_max_tokens = int(max_token_limits - self.get_output_limit())
        first_token_cnt = token_cnts[0][0]

        for i in range(len(token_cnts) - pivot, 0, -1):
            token_cnt = token_cnts[i][0]
            split_idx = i + 1
            if first_token_cnt + tail_tokens + token_cnt >= possible_max_tokens:
                head = messages[1:split_idx]
                head_token_cnts = self.tokenize(head)
                head_token_cnt = sum([token_cnt for token_cnt, _ in head_token_cnts])
                logger.debug(
                    f"head_token_cnt: {head_token_cnt}, possible_max_tokens:"
                    f" {possible_max_tokens}"
                )
                if head_token_cnt < max_token_limits:
                    break
            tail_tokens += token_cnt

        first = messages[0]
        head = messages[1:split_idx]
        tail = messages[split_idx:]

        logger.debug(
            f"pivot: {pivot}, split_idx: {split_idx}, len(token_cnts):"
            f" {len(token_cnts)}, len(messages): {len(messages)}"
        )

        return first, head, tail, split_idx

    def _process_head_section(
        self, head: list[BaseMessage], max_token_limits: int
    ) -> list[BaseMessage]:
        """Process the head section of messages."""
        head_token_cnts = self.tokenize(head)
        head_token_cnt = sum([token_cnt for token_cnt, _ in head_token_cnts])

        if head_token_cnt > max_token_limits:
            return self.summarize(head)
        else:
            return self._summarize_all(head)

    def _combine_message_sections(
        self,
        first: BaseMessage,
        summarized_head: list[BaseMessage],
        tail: list[BaseMessage],
        max_token_limits: int,
    ) -> list[BaseMessage]:
        """Combine message sections and check if further summarization is needed."""
        messages = [first] + summarized_head + tail
        token_cnts = self.tokenize(messages)
        total_token_cnt = sum([token_cnt for token_cnt, _ in token_cnts])

        if total_token_cnt < max_token_limits:
            return messages

        return self.summarize(messages)

    def _summarize_all(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Create a summary of all messages in the conversation."""
        content = self._format_messages_for_summary(messages)
        summarize_message = self._create_summary_prompt()

        summarize_messages = [
            HumanMessage(content=summarize_message),
            HumanMessage(content=content),
        ]

        response = self.summarize_chat_model.invoke(summarize_messages)
        response.id = messages[0].id

        # Create result with response and RemoveMessages for original messages
        result = [response]
        for m in messages[1:]:
            result.append(RemoveMessage(id=m.id))

        return result

    def _format_messages_for_summary(self, messages: list[BaseMessage]) -> str:
        """Format messages for the summary process."""
        content = ""
        for message in messages:
            if isinstance(message, HumanMessage):
                content += f"Human: {message.content}"
            elif isinstance(message, AIMessage):
                content += f"AI: {message.content}"

            if not content.endswith("\n"):
                content += "\n"
        return content

    def _create_summary_prompt(self) -> str:
        """Create the prompt for summarization."""
        return (
            "Create a detailed summary of the conversation. "
            + "Especially, if the message is from tool, try to preserve meaningful "
            + "result.\n\n\n"
        )

    def refine_tools_output(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        return self.summarize(messages)

    def _get_model_settings(self) -> tuple[int, int]:
        """Get timeout and max retries based on model type."""
        if "gemini" in self.model_name:
            return self.gc.gemini_timeout, self.gc.gemini_max_retries
        elif self.model_name in [ATLANTA_CHAT, ATLANTA_TOOL, ATLANTA_REASONING]:
            return self.gc.atlanta_timeout, self.gc.atlanta_max_retries
        else:  # Default to OpenAI settings
            return self.gc.openai_timeout, self.gc.openai_max_retries

    def ask_and_repeat_until(
        self,
        verifier: Callable[..., T],
        messages: list,
        default: T,
        recoverer: Optional[Callable[..., T]] = None,
        max_retries: Optional[int] = None,
        try_with_error: bool = True,
        pass_retries_to_verifier: bool = False,
        cache: bool = False,
        cache_index: int = 0,
    ) -> T:
        """
        Ask the user for input and repeat until the input satisfies the given condition.

        Args:
            f (Callable[[str], bool]): The condition to satisfy.
            prompt (str): The prompt to display to the user.

        Returns:
            str: The input that satisfies the condition.
        """
        if max_retries is None:
            max_retries = self._get_model_settings()[1]

        messages_copy = deepcopy(messages)

        if cache:
            # Lets caching only for claude
            if self.model_name.startswith("claude"):
                add_cache_control(messages_copy[0])
                add_cache_control(messages_copy[cache_index])

        idx = 0
        response = None
        large_model_used = False

        def mark_large_model_used():
            nonlocal large_model_used
            large_model_used = True

        while idx < max_retries:
            try:
                responses = self.invoke(
                    messages_copy,
                    force_large_model=large_model_used,
                    large_model_callback=mark_large_model_used,
                )
                response = responses[-1]

                if pass_retries_to_verifier:
                    return verifier(response, max_retries - idx - 1)

                return verifier(response)
            except Exception as e:
                # This exception handling is only for exceptions from verifier
                idx += 1
                if idx == max_retries:
                    break
                response_str = None
                if try_with_error:
                    if isinstance(response, BaseMessage):
                        # response_str = response.content
                        messages_copy.append(response)

                    msg = ASK_AND_REPEAT_UNTIL_MSG.format(
                        # response_str=response_str,
                        e=e
                    )

                    # We don't need to remove any message from the original msg
                    # as invoke() now uses deepcopy and doesn't modify the original msg.
                    # The response is included in the error message, so we just append
                    # the new message without removing anything.
                    # messages_copy.pop()

                    messages_copy.append(HumanMessage(msg))
                logger.warning(f"Trying to solving error: {e}")
                # logger.warning(traceback.format_exc())
                if response_str:
                    logger.debug(f"   - {response_str}")
        try:
            if response and recoverer:
                return recoverer(response)
        except Exception:
            pass

        return default

    async def aask_and_repeat_until(
        self,
        verifier: Callable[..., T],
        messages: list,
        default: T,
        recoverer: Optional[Callable[..., T]] = None,
        max_retries: Optional[int] = None,
        try_with_error: bool = True,
        pass_retries_to_verifier: bool = False,
        cache: bool = False,
        cache_index: int = 0,
    ) -> T:
        """
        Async version of ask_and_repeat_until.
        Ask the user for input and repeat until the input satisfies the given condition.

        Args:
            f (Callable[[str], bool]): The condition to satisfy.
            prompt (str): The prompt to display to the user.

        Returns:
            str: The input that satisfies the condition.
        """
        if max_retries is None:
            max_retries = self._get_model_settings()[1]

        messages_copy = deepcopy(messages)

        if cache:
            # Lets caching only for claude
            if self.model_name.startswith("claude"):
                add_cache_control(messages_copy[0])
                add_cache_control(messages_copy[cache_index])

        idx = 0
        response = None
        large_model_used = False

        def mark_large_model_used():
            nonlocal large_model_used
            large_model_used = True

        while idx < max_retries:
            try:
                responses = await self.ainvoke(
                    messages_copy,
                    force_large_model=large_model_used,
                    large_model_callback=mark_large_model_used,
                )
                response = responses[-1]

                if pass_retries_to_verifier:
                    return verifier(response, max_retries - idx - 1)

                return verifier(response)
            except Exception as e:
                # This exception handling is only for exceptions from verifier
                idx += 1
                if idx == max_retries:
                    break
                response_str = None
                if try_with_error:
                    if isinstance(response, BaseMessage):
                        messages_copy.append(response)

                    msg = ASK_AND_REPEAT_UNTIL_MSG.format(e=e)
                    messages_copy.append(HumanMessage(msg))
                logger.warning(f"Trying to solving error: {e}")
                if response_str:
                    logger.debug(f"   - {response_str}")
        try:
            if response and recoverer:
                return recoverer(response)
        except Exception:
            pass

        return default
