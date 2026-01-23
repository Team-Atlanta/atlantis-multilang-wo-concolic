from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.graph import MessagesState

from ..prompts.autopa import SYSTEM
from ..utils.agent import BaseAgentTemplate
from ..utils.context import GlobalContext


class AutoPrompterInput(MessagesState):
    pass


class AutoPrompterOutput(MessagesState):
    generated_prompt: str


class AutoPrompterState(AutoPrompterInput, AutoPrompterOutput):
    pass


class AutoPrompter(BaseAgentTemplate):
    def __init__(self, gc: GlobalContext):
        from ..utils.llm import LLM

        ret_dir = gc.RESULT_DIR / "autoprompt"

        super().__init__(
            gc, ret_dir, AutoPrompterInput, AutoPrompterOutput, AutoPrompterState
        )

        self.builder.add_node("autoprompt", self.autoprompt)

        self.builder.add_edge("preprocess", "autoprompt")
        self.builder.add_edge("autoprompt", "finalize")

        self.llm = LLM("llama-3.1-70b", gc)

    def deserialize(self, state, content: str) -> dict:
        # TODO
        return {}

    def serialize(self, state) -> str:
        # TODO
        return ""

    def preprocess(self, state):
        pass

    def finalize(self, state):
        pass

    def autoprompt(self, state: AutoPrompterInput) -> AutoPrompterOutput:
        # TODO: handle system and human messages differently?

        messages = [HumanMessage(SYSTEM)] + state["messages"]
        responses = self.llm.invoke(messages)
        response = responses[-1]

        state = AutoPrompterOutput(
            generated_prompt=response.content,
        )

        return state


if __name__ == "__main__":
    cp_path = Path("../../asc-challenge-002-jenkins-cp")
    gc = GlobalContext(False, cp_path, [])
    autoprompter = AutoPrompter(gc)
