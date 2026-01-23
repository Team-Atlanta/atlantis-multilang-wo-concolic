import os
from typing import Optional

from loguru import logger

from ...utils.agent import GENERATOR_AGENT, BaseAgentTemplate
from ...utils.context import GlobalContext
from ...utils.llm import LLM
from .graph import build_main_graph
from .prompts import build_prompts
from .state import (
    GeneratorAgentInputState,
    GeneratorAgentOutputState,
    GeneratorAgentOverallState,
    GeneratorPayload,
)


class GeneratorAgent(BaseAgentTemplate):
    def __init__(
        self, gc: GlobalContext, llm: Optional[LLM] = None, enable_usage_snapshot=False
    ):
        """Initialize agent with GlobalContext and optional LLM."""
        ret_dir = gc.RESULT_DIR / GENERATOR_AGENT
        super().__init__(
            gc,
            ret_dir,
            GeneratorAgentInputState,
            GeneratorAgentOutputState,
            GeneratorAgentOverallState,
            enable_usage_snapshot=enable_usage_snapshot,
        )

        # Initialize LLM
        self.llm = llm or LLM(
            model=os.getenv("BGA_GENERATOR_MODEL", "gpt-4o"),
            config=gc,
            temperature=float(os.getenv("BGA_GENERATOR_TEMPERATURE", "0.4")),
            max_tokens=int(os.getenv("BGA_GENERATOR_MAX_TOKENS", "4096")),
            prompt_caching=False,
        )

    def compile(self):
        """Build and compile the workflow graph."""
        return build_main_graph(self)

    def preprocess(self, state: GeneratorAgentInputState) -> GeneratorAgentOverallState:
        """Preprocess input state."""
        if self.gc.no_llm:
            return self.gc.get_state()

        # Add LLM and retry counter to state
        new_state = GeneratorAgentOverallState(
            **state,
            gc=self.gc,
            llm=self.llm,
            iter_cnt=0,
            crashed_blobs={},
            error={},
            messages=[],
        )

        new_state["cp_name"] = self.gc.cp.name
        new_state["harness_name"] = self.gc.target_harness
        new_state["payload"] = GeneratorPayload()

        if state.get("standalone") or state.get("source_path"):
            # Standalone mode
            from pathlib import Path

            src_path = Path(state["source_path"])
            assert src_path and src_path.exists()
            source_code = src_path.read_text()

            # diff code is optional
            diff_code = ""
            if state.get("diff_path"):
                diff_path = Path(state["diff_path"])
                if diff_path and diff_path.is_file():
                    logger.info(
                        f"We are in the delta mode. Loading diff at {diff_path}"
                    )
                    diff_code = diff_path.read_text()

                # check diff code
                if diff_code:
                    num_lines = len(diff_code.splitlines())
                    # we will not consider big diffs
                    if num_lines > 1000:
                        logger.info(
                            "Diff file is too big "
                            f"({num_lines} lines). "
                            "Running without diff."
                        )
                        diff_code = ""

            add_cmdinjection = self.gc.cp.language == "jvm"

            new_state["messages"] = build_prompts(
                add_system=True,
                add_known_struct=True,
                # add_exploit_sentinel=True,
                src_path=src_path,
                source_code=source_code,
                diff_code=diff_code,
                # sanitizers=new_state["selected_sanitizers"],
                sanitizer=state["sanitizer"],
                cp_name=self.gc.cp.name,
                harness_name=self.gc.target_harness,
                add_cmdinjection=add_cmdinjection,
            )
            new_state["standalone"] = True

            logger.info("Running standalone mode ...")

        else:
            # General generator
            # This should be updated in targetted generator
            attr_cg = state["attr_cg"]
            src_func = state["src_func"]
            dst_func = state["dst_func"]
            if not attr_cg or not src_func or not dst_func:
                raise ValueError("src_func and dst_func should be defined.")

            # Handle missing sanitizer - derive it
            sanitizer = state.get("sanitizer")
            if not sanitizer:
                if self.gc.cp.language == "jvm":
                    sanitizer = "jazzer"
                else:
                    sanitizer = self.gc.cp.sanitizers[0]

            # Handle selected_sanitizers (can be None/empty)
            selected_sanitizers = state.get("selected_sanitizers") or []

            # Update state
            new_state["sanitizer"] = sanitizer
            new_state["selected_sanitizers"] = selected_sanitizers

            # Ensure payload exists
            if "payload" not in new_state:
                raise ValueError("payload should be defined.")

            new_state["payload"]["prev_coverage_info"] = attr_cg.coverage_info

            new_state["messages"] = build_prompts(
                add_system=True,
                add_known_struct=True,
                attr_cg=attr_cg,
                src_func=src_func,
                dst_func=dst_func,
                sanitizers=selected_sanitizers,
                sanitizer=sanitizer,
                cp_name=self.gc.cp.name,
                harness_name=self.gc.target_harness,
            )
            new_state["standalone"] = False

            logger.info("Running with bug information ...")

        return new_state

    def finalize(self, state: GeneratorAgentOverallState) -> GeneratorAgentOutputState:
        """Finalize the generator process and store results."""
        # Store final results if we have them
        # payload = state.get("payload", {})
        crashed_blobs = state.get("crashed_blobs", {})

        return GeneratorAgentOutputState(
            crashed_blobs=crashed_blobs,
            error=state.get("error", {}),
        )

    def serialize(self, state) -> str:
        # TODO
        return ""

    def deserialize(self, state, content: str) -> dict:
        # TODO
        return {}
