import inspect
from typing import Callable, TypeVar, Union

from dateutil import parser
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.utils import get_buffer_string
from loguru import logger
from tokencost import TOKEN_COSTS, count_string_tokens

from tools.context import ReverserContext
T = TypeVar("T")


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


class LLM:
    chat_model: BaseChatModel
    model_name: str

    def __init__(
        self,
        model: str,
        config: ReverserContext,
        tools=None,
        output_format=None,
        temperature=0,
        max_tokens=None,
        model_kwargs=None,
    ):

        from langchain_openai import ChatOpenAI
        from langchain_anthropic import ChatAnthropic

        temperature = temperature
        if model in ["o1-mini", "o1-preview"]:
            temperature = 1

        chat_model = ChatAnthropic(
            model=model,
            temperature=temperature,
            api_key=config.api_key,
            base_url=config.base_url,
            max_tokens=max_tokens,
            model_kwargs=model_kwargs,
        )

        self.chat_model = chat_model

        assert (
            tools is None or output_format is None
        ), "Only one of tools or output_format should be provided."

        if tools is not None:
            chat_model = chat_model.bind_tools(tools)

        elif output_format is not None:
            chat_model = chat_model.with_structured_output(output_format)

        self.runnable_chat_model = chat_model
        self.model_name = model
        self.gc = config

    def get_context_limit(self) -> int:
        """Returns the token limit for the given model name.

        Args:
            model_name (str): The name of the model.

        Returns:
            int: The token limit for the model.
        """
        model_data = TOKEN_COSTS.get(self.model_name, None)
        if model_data is None:
            logger.warning(f"Model {self.model_name} not found in TOKEN_COSTS")
            return 8192
        return model_data.get("max_input_tokens", 8192)

    def get_output_limit(self) -> int:
        """Returns the token limit for the given model name.

        Args:
            model_name (str): The name of the model.

        Returns:
            int: The token limit for the model.
        """
        model_data = TOKEN_COSTS.get(self.model_name, None)
        if model_data is None:
            logger.error(f"Model {self.model_name} not found in TOKEN_COSTS")
            return 4096
        return model_data.get("max_output_tokens", 4096)

    def invoke(
        self,
        messages: list[BaseMessage],
        **kwargs,
    ) -> list[BaseMessage]:
        """Invoke the model with the given messages.
        This function returns the messages with the model's response appended.

        Args:
            messages (list[BaseMessage]): The messages to send to the model.
            choice (AutoPromptChoice): The choice of autoprompt to use.
            model (BaseChatModel, optional): The model to use. Defaults to None.

        Returns:
            list[BaseMessage]: The updated messages.
        """

        response = self.runnable_chat_model.invoke(messages, **kwargs)
        #messages.append(response)
        return messages + [response]

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        max_tokens=None,
        **kwargs,
    ) -> list[BaseMessage]:
        """Invoke the model with the given messages asynchronously."""
        llm = self.runnable_chat_model
        if max_tokens:
            llm = llm.bind(max_tokens=max_tokens)
        response = await llm.ainvoke(messages, **kwargs)
        return messages + [response]

    def tokenize(self, messages: list[BaseMessage]) -> list[tuple[int, BaseMessage]]:
        token_cnts = []
        for msg in messages:
            msg_str = get_buffer_string([msg])
            token_cnt = count_string_tokens(msg_str, "gpt-4o")
            token_cnts.append((token_cnt, msg))
        return token_cnts

    def refine_tools_output(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        return self.summarize(messages)

    def ask_and_repeat_until(
        self,
        verifier: Callable[..., T],
        messages: list,
        default: T,
        n: int = 5,
        try_with_error: bool = True,
    ) -> T:
        """
        Ask the user for input and repeat until the input satisfies the given condition.

        Args:
            f (Callable[[str], bool]): The condition to satisfy.
            prompt (str): The prompt to display to the user.

        Returns:
            str: The input that satisfies the condition.
        """

        for m in messages:
            if isinstance(m, BaseMessage):
                m.content = inspect.cleandoc(m.content)

        idx = 0
        while idx < n:
            try:
                responses = self.invoke(messages)
                response = responses[-1]
                return verifier(response)
            except Exception as e:
                if try_with_error:
                    if isinstance(response, BaseMessage):
                        response = response.content

                    msg = f"""Your previous answer was not valid. Please try again.

                    The error was: {e}

                    Please change your response in accordance with the error.
                    """

                    # since we already give back the response with this message,
                    # we don't need to keep the actual response which might be
                    # the wrong type (if structured outputs is used)
                    # messages.pop()

                    messages.append(HumanMessage(inspect.cleandoc(msg)))

                logger.error(f"Error: {e}")
                logger.error(f"   - {response}")
                idx += 1
                continue
        return default
