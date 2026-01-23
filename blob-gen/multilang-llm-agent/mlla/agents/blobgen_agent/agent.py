import datetime
import os
from typing import Dict, Optional

from loguru import logger

from ...utils.agent import BLOBGEN_AGENT, BaseAgentTemplate
from ...utils.context import GlobalContext
from ...utils.llm import LLM
from .graph import build_main_graph

# Build initial prompts
from .prompts.build_prompts import build_prompts
from .state import (
    BlobGenAgentInputState,
    BlobGenAgentOutputState,
    BlobGenAgentOverallState,
    BlobGenPayload,
)


class BlobGenAgent(BaseAgentTemplate):
    """Agent for generating and validating payload blobs for a single CG."""

    def __init__(
        self,
        config: GlobalContext,
        llm: Optional[LLM] = None,
        enable_usage_snapshot=False,
    ):
        """Initialize the agent."""
        ret_dir = config.RESULT_DIR / BLOBGEN_AGENT
        super().__init__(
            config,
            ret_dir,
            BlobGenAgentInputState,
            BlobGenAgentOutputState,
            BlobGenAgentOverallState,
            enable_usage_snapshot=enable_usage_snapshot,
        )

        # Initialize LLM
        self.llm = llm or LLM(
            model=os.getenv("BGA_MODEL", "gpt-4o"),
            config=config,
            temperature=float(os.getenv("BGA_TEMPERATURE", "0.4")),
            max_tokens=int(os.getenv("BGA_MAX_TOKENS", "4096")),
            prompt_caching=False,
        )

    def compile(self):
        return build_main_graph(self)

    def preprocess(
        self, input_state: BlobGenAgentInputState
    ) -> BlobGenAgentOverallState:
        """Initialize the state for processing."""
        # Create overall state with default values
        state = BlobGenAgentOverallState(
            **input_state,
            gc=self.gc,
            llm=self.llm,
            iter_cnt=0,
            cp_name=self.gc.cp.name,
            # output status
            status="success",
            error={},
            payload_dict={},
            crashed_blobs={},
            current_payload=BlobGenPayload(attr_cg=input_state.get("attr_cg")),
            start_time=datetime.datetime.now(),
            messages=[],
        )

        # Log start of processing
        logger.info(
            f"Starting processing for CG {state.get('cg_name')} "
            f"with sanitizer {state.get('sanitizer')}"
        )

        # Check if we have a BIT
        if (
            state.get("attr_cg")
            and state["attr_cg"].bit_node
            and state["attr_cg"].bit_node.bit_info
        ):
            bit = state["attr_cg"].bit_node.bit_info
            path = bit.func_location.file_path
            func_name = bit.func_location.func_name
            func_name = " ".join(map(lambda x: x.strip(), func_name.split()))
            vuln_type = bit.analysis_message[0].sanitizer_type
            start_line = bit.func_location.start_line
            end_line = bit.func_location.end_line
            logger.info(
                f"Target function for CG {state.get('cg_name')}:\n"
                f"   - Path: {path}\n"
                f"   - [{vuln_type}] {func_name}: line: {start_line} - {end_line}\n"
            )

        # Build initial prompts
        selected_sanitizers = state.get("selected_sanitizers", [])
        if selected_sanitizers:
            state["messages"] = build_prompts(
                add_system=True,
                attr_cg=state.get("attr_cg"),
                cp_name=state["cp_name"],
                harness_name=state["harness_name"],
                sanitizers=selected_sanitizers,
            )
        else:
            # sanitizer will be selected using the select_sanitizer node
            state["messages"] = build_prompts(
                add_system=True,
                attr_cg=state.get("attr_cg"),
                cp_name=state["cp_name"],
                harness_name=state["harness_name"],
            )

        return state

    def finalize(self, state: BlobGenAgentOverallState) -> Dict:
        """Finalize processing and prepare the output state."""
        # Log completion
        status = state.get("status", "failed")
        cg_name = state.get("cg_name", "unknown")
        processing_time = (datetime.datetime.now() - state["start_time"]).seconds

        if status == "crashed":
            num_crashed = len(state.get("crashed_blobs", {}))
            logger.info(
                f"Completed processing for CG {cg_name} with {num_crashed} crashes "
                f"in {processing_time:.2f}s"
            )
        elif status == "success":
            logger.info(
                f"Completed processing for CG {cg_name} (no crashes) "
                f"in {processing_time:.2f}s"
            )
        else:
            logger.error(
                f"Failed processing for CG {cg_name}: in {processing_time:.2f}s"
            )

        new_state: Dict = {}
        current_payload = state.get("current_payload", {})
        if current_payload:
            blob_hash = current_payload.get("blob_hash", "")
            if blob_hash:
                payload_dict = state["payload_dict"].copy()
                payload_dict.update({blob_hash: current_payload})
                new_state["payload_dict"] = payload_dict

        return new_state

    def serialize(self, state) -> str:
        """Serialize the state to a string."""
        # TODO: Implement serialization
        return ""

    def deserialize(self, state, content: str) -> dict:
        """Deserialize the content to a state dictionary."""
        # TODO: Implement deserialization
        return {}
