from langchain_core.messages import BaseMessage
from typing_extensions import Dict, List, Optional, TypedDict

from ...utils.attribute_cg import AttributeCG, AttributeFuncInfo
from ...utils.bit import BugInducingThing
from ...utils.context import GlobalContext
from ...utils.llm import LLM


class GeneratorPayload(TypedDict, total=False):
    """Individual payload with its own coverage tracking."""

    # Generator planning and creation
    generator_code: str  # Generated code for creating variations
    generator_desc: str  # Description of the generator
    generator_hash: str
    generator_blobs: List[bytes]  # Generated blob variations

    # Coverage analysis
    coverage_results: List[Dict]  # Coverage info for each variation
    merged_coverage: Dict  # Merged coverage from all variations
    prev_coverage_info: Dict
    coverage_diff: Dict
    coverage_stats: Dict
    coverage_diff_str: str


class GeneratorAgentInputState(TypedDict, total=False):
    """Input state for Generator Agent."""

    harness_name: str
    attr_cg: AttributeCG
    payload: GeneratorPayload
    src_func: AttributeFuncInfo
    dst_func: AttributeFuncInfo
    sanitizer: str
    selected_sanitizers: List[str]

    standalone: bool
    source_path: str
    diff_path: str
    run_sanitizer_selection: bool
    bit: Optional[BugInducingThing]  # BIT associated with this CG (if any)


class GeneratorAgentOutputState(TypedDict):
    """Output state for Generator Agent."""

    # payload: Payload  # Updated payload with generator results
    crashed_blobs: Dict[str, bytes]
    error: Dict[str, str]


class GeneratorAgentOverallState(
    GeneratorAgentInputState, GeneratorAgentOutputState, total=False
):
    """Combined state for Generator Agent."""

    # Added in preprocess
    gc: GlobalContext
    llm: LLM
    iter_cnt: int  # Track number of improvement attempts
    cp_name: str

    messages: List[BaseMessage]
    crashed: bool
