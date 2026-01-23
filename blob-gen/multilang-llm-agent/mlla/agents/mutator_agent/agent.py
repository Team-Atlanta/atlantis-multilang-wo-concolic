import os
from typing import Optional

from loguru import logger

from ...utils.agent import MUTATOR_AGENT, BaseAgentTemplate
from ...utils.context import GlobalContext
from ...utils.llm import LLM
from .graph import build_main_graph
from .prompts import build_prompts
from .state import (
    MutatorAgentInputState,
    MutatorAgentOutputState,
    MutatorAgentOverallState,
    MutatorPayload,
)


class MutatorAgent(BaseAgentTemplate):
    def __init__(
        self, gc: GlobalContext, llm: Optional[LLM] = None, enable_usage_snapshot=False
    ):
        """Initialize agent with GlobalContext and optional LLM."""
        ret_dir = gc.RESULT_DIR / MUTATOR_AGENT
        super().__init__(
            gc,
            ret_dir,
            MutatorAgentInputState,
            MutatorAgentOutputState,
            MutatorAgentOverallState,
            enable_usage_snapshot=enable_usage_snapshot,
        )

        # Initialize LLM
        self.llm = llm or LLM(
            model=os.getenv("BGA_MUTATOR_MODEL", "gpt-4o"),
            config=gc,
            temperature=float(os.getenv("BGA_MUTATOR_TEMPERATURE", "0.4")),
            max_tokens=int(os.getenv("BGA_MUTATOR_MAX_TOKENS", "4096")),
            prompt_caching=False,
        )

    def compile(self):
        """Build and compile the workflow graph."""
        return build_main_graph(self)

    def preprocess(self, state: MutatorAgentInputState) -> MutatorAgentOverallState:
        """Preprocess input state."""
        if self.gc.no_llm:
            return self.gc.get_state()

        # Add LLM and retry counter to state
        new_state = MutatorAgentOverallState(
            **state,
            gc=self.gc,
            llm=self.llm,
            iter_cnt=0,
            language=self.gc.cp.language,
            current_mutator=MutatorPayload(),
            mutator_dict={},
            error={},
            messages=[],
        )

        # This should be updated in targetted mutator
        attr_cg = state["attr_cg"]
        src_func = state["src_func"]
        dst_func = state["dst_func"]
        if not attr_cg or not src_func or not dst_func:
            raise ValueError("src_func and dst_func should be defined.")

        # Initialize messages with system prompt and base context
        new_state["messages"] = build_prompts(
            add_system=True,
            add_known_struct=True,
            attr_cg=attr_cg,
            src_func=src_func,
            dst_func=dst_func,
        )

        return new_state

    def finalize(self, state: MutatorAgentOverallState) -> MutatorAgentOutputState:
        """Finalize the mutator process and return results."""
        # Extract mutator dict from state
        mutator_dict = state.get("mutator_dict", {})
        # Log completion
        if mutator_dict:
            logger.debug(f"Successfully created {len(mutator_dict)} mutators")
        else:
            logger.warning("No mutators were created")

        # Return output state
        return MutatorAgentOutputState(
            mutator_dict=mutator_dict,
            error=state.get("error", {}),
        )

    def serialize(self, state) -> str:
        # TODO
        return ""

    def deserialize(self, state, content: str) -> dict:
        # TODO
        return {}
