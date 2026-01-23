"""OrchestratorAgent that coordinates BlobGenAgent, GeneratorAgent, and MutatorAgent."""

import os
from typing import Dict, Optional

from loguru import logger

from ...utils.agent import ORCHESTRATOR_AGENT, BaseAgentTemplate
from ...utils.context import GlobalContext
from ...utils.llm import LLM
from .modules import create_blobgen_contexts, run_all_agents
from .state import (
    OrchestratorAgentInputState,
    OrchestratorAgentOutputState,
    OrchestratorAgentOverallState,
)


class OrchestratorAgent(BaseAgentTemplate):
    """Agent that orchestrates BlobGenAgent, GeneratorAgent, and MutatorAgent."""

    def __init__(
        self,
        config: GlobalContext,
        llm: Optional[LLM] = None,
        enable_usage_snapshot=True,
    ):
        """Initialize the agent."""
        ret_dir = config.RESULT_DIR / ORCHESTRATOR_AGENT
        super().__init__(
            config,
            ret_dir,
            OrchestratorAgentInputState,
            OrchestratorAgentOutputState,
            OrchestratorAgentOverallState,
            enable_usage_snapshot=enable_usage_snapshot,
        )

        # Initialize LLM
        self.llm = llm or LLM(
            model=os.getenv("ORCHESTRATOR_MODEL", "gpt-4o"),
            config=config,
            temperature=float(os.getenv("ORCHESTRATOR_TEMPERATURE", "0.4")),
            max_tokens=int(os.getenv("ORCHESTRATOR_MAX_TOKENS", "4096")),
            prompt_caching=False,
        )

    def compile(self):
        # Define workflow
        self.builder.add_node("run_all_agents", run_all_agents)

        # Define edges
        self.builder.add_edge("preprocess", "run_all_agents")
        self.builder.add_edge("run_all_agents", "finalize")

        return self.builder.compile()

    def preprocess(
        self, input_state: OrchestratorAgentInputState
    ) -> OrchestratorAgentOverallState:
        """Initialize the state for processing."""
        # Determine sanitizer based on language
        if self.gc.cp.language == "jvm":
            sanitizer = "jazzer"
        else:
            sanitizer = input_state.get("sanitizer", "address")

        # Create contexts for BlobGenAgent
        blobgen_contexts = create_blobgen_contexts(
            self.gc,
            input_state.get("CGs", {}),
            input_state.get("BITs", []),
            sanitizer,
        )

        # Create overall state
        state = OrchestratorAgentOverallState(
            **input_state,
            gc=self.gc,
            llm=self.llm,
            blobgen_contexts=blobgen_contexts,
            sanitizer=sanitizer,
            blobgen_results={},
            generator_results={},
            mutator_results={},
            status="success",
            error={},
            transitions=[],
        )

        logger.info(f"Created {len(blobgen_contexts)} contexts for BlobGenAgent")
        return state

    def finalize(
        self, state: OrchestratorAgentOverallState
    ) -> OrchestratorAgentOutputState:
        """Finalize processing and prepare the output state."""
        # Log completion
        blobgen_count = len(state.get("blobgen_results", {}))
        generator_count = len(state.get("generator_results", {}))
        mutator_count = len(state.get("mutator_results", {}))

        logger.info(
            f"Completed orchestration with {blobgen_count} BlobGen results, "
            f"{generator_count} Generator results, and {mutator_count} Mutator results"
        )

        return OrchestratorAgentOutputState(
            blobgen_results=state.get("blobgen_results", {}),
            generator_results=state.get("generator_results", {}),
            mutator_results=state.get("mutator_results", {}),
            status=state.get("status", "success"),
            error=state.get("error", {}),
        )

    def serialize(self, state) -> str:
        """Serialize the state to a string."""
        # TODO: Implement serialization
        return ""

    def deserialize(self, state, content: str) -> Dict:
        """Deserialize the content to a state dictionary."""
        # TODO: Implement deserialization
        return {}
