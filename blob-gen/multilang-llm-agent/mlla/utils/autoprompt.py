from langchain_core.messages import BaseMessage, HumanMessage

from .constants import AutoPromptChoice
from .context import GlobalContext


def generate_prompt_by_choice(
    messages: list[BaseMessage], gc: GlobalContext, choice: AutoPromptChoice
) -> list[BaseMessage]:
    first_message = messages[0]
    if choice == AutoPromptChoice.AUTO:
        from ..agents.autopa import AutoPrompter

        autoprompter = AutoPrompter(gc)
        graph = autoprompter.compile()
        output = graph.invoke(
            {
                "messages": messages,
            },
            gc.graph_config,
        )
        generated_prompt = output["generated_prompt"]
        return [HumanMessage(generated_prompt)]

    elif choice == AutoPromptChoice.FEW_SHOTS:
        # TODO: Team-Atlanta/multilang-llm-agent#35
        pass
    elif choice == AutoPromptChoice.COT:
        if "Think step by step." not in first_message.content:
            messages[0].content += "\nThink step by step.\n"
    else:
        pass

    return messages


def generate_prompt(
    messages: list[BaseMessage],
    gc: GlobalContext,
    choice: AutoPromptChoice | list[AutoPromptChoice],
) -> list[BaseMessage]:
    if isinstance(choice, list):
        # Order matters
        for c in choice:
            messages = generate_prompt_by_choice(messages, gc, c)
    else:
        messages = generate_prompt_by_choice(messages, gc, choice)

    return messages
