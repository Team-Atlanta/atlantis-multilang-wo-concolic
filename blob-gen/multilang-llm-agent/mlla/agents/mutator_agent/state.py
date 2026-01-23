from langchain_core.messages import BaseMessage
from typing_extensions import Dict, List, TypedDict

from ...utils.attribute_cg import AttributeCG, AttributeFuncInfo
from ...utils.context import GlobalContext
from ...utils.llm import LLM


class MutatorPayload(TypedDict, total=False):
    """Mutator payload with coverage tracking."""

    mutator_plan: str
    mutator_code: str
    mutator_desc: str
    mutator_hash: str
    mutator_feedback: str


class MutatorAgentInputState(TypedDict):
    """Input state for Mutator Agent."""

    harness_name: str
    attr_cg: AttributeCG
    src_func: AttributeFuncInfo
    dst_func: AttributeFuncInfo
    # sanitizers: List[str] # Not used for now


class MutatorAgentOutputState(TypedDict):
    """Output state for Mutator Agent."""

    mutator_dict: Dict[str, MutatorPayload]
    error: Dict[str, str]


class MutatorAgentOverallState(MutatorAgentInputState, MutatorAgentOutputState):
    """Combined state for Mutator Agent."""

    # Added in preprocess
    gc: GlobalContext
    llm: LLM
    language: str
    iter_cnt: int  # Track number of improvement attempts

    current_mutator: MutatorPayload  # Current mutator being improved
    messages: List[BaseMessage]
