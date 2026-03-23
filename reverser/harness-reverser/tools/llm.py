from dateutil import parser
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from loguru import logger
from tokencost import TOKEN_COSTS

from tools.context import ReverserContext


def standardize_model_name(model_name: str) -> str:
    model_name = model_name.lower()

    # "openai/oai-gpt-5.4" -> "oai-gpt-5.4"
    if "/" in model_name:
        model_name = model_name.split("/")[-1]

    # "oai-gpt-5.4" -> "gpt-5.4"
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
        if model in ["gpt-5.4-mini", "gpt-5.4"]:
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
