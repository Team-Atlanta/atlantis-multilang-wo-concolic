"""State definitions for OrchestratorAgent."""

from typing_extensions import Any, Dict, List, Literal, Optional, TypedDict

from ...utils.attribute_cg import AttributeCG
from ...utils.bit import BugInducingThing
from ...utils.context import GlobalContext
from ...utils.cp import sCP
from ...utils.llm import LLM
from ..blobgen_agent.state import BlobGenPayload
from ..generator_agent.state import GeneratorPayload
from ..mutator_agent.state import MutatorPayload


class BlobGenContext(TypedDict):
    """Context for processing a specific CG with BlobGenAgent."""

    harness_name: str
    sanitizer: str
    cg_name: str
    attr_cg: Optional[AttributeCG]
    bit: Optional[BugInducingThing]
    selected_sanitizers: List[str]


class OrchestratorAgentInputState(TypedDict):
    """Input state for OrchestratorAgent."""

    cp: sCP
    CGs: Dict[str, List]
    BITs: List[BugInducingThing]
    sanitizer: str


class OrchestratorAgentOutputState(TypedDict):
    """Output state for OrchestratorAgent."""

    blobgen_results: Dict[str, BlobGenPayload]
    generator_results: Dict[str, GeneratorPayload]
    mutator_results: Dict[str, MutatorPayload]
    status: Literal["success", "partial_success", "failed"]
    error: Dict[str, Any]


class OrchestratorAgentOverallState(
    OrchestratorAgentInputState, OrchestratorAgentOutputState
):
    """Overall state for OrchestratorAgent."""

    gc: GlobalContext
    llm: LLM
    blobgen_contexts: List[BlobGenContext]
    transitions: List[tuple]  # Collected transitions for MutatorAgent
