import datetime

from langchain_core.messages import BaseMessage
from typing_extensions import Annotated, Dict, List, Optional, TypedDict

from mlla.utils.state import merge_with_update

from ...utils.attribute_cg import AttributeCG
from ...utils.bit import BugInducingThing
from ...utils.context import GlobalContext
from ...utils.llm import LLM
from ...utils.run_pov import RunPovResult


class BlobGenPayload(TypedDict, total=False):
    """Individual payload with its own coverage tracking."""

    # Context information
    attr_cg: Optional[AttributeCG]
    selected_sanitizers: List[str]  # Sanitizers selected for this CG-BIT mapping

    # Basic payload information
    code: str  # Payload code
    desc: str  # Payload description
    blob: bytes  # Payload blob
    blob_hash: str  # Hash of the blob

    # Analysis information
    failure_explanation: str  # Explanation of failure (if any)
    crashed: bool  # Whether this payload caused a crash
    sanitizer_info: Optional[str]  # Sanitizer information

    # Coverage and analysis
    coverage_info: Dict  # Coverage specific to this payload

    # Execution results
    run_pov_result: Optional[RunPovResult]  # Result of running the payload


class BlobGenAgentInputState(TypedDict):
    """Input state for BlobGenAgent."""

    # Basic information
    harness_name: str  # Name of the harness
    sanitizer: str  # Base sanitizer type to use
    # Selected sanitizers for this context
    selected_sanitizers: List[str]  # Sanitizers selected for this CG-BIT mapping
    cg_name: str  # Name of the call graph

    # Core components
    attr_cg: AttributeCG  # AttributeCG for this context
    bit: Optional[BugInducingThing]  # BIT associated with this CG (if any)
    run_sanitizer_selection: bool  # Whether to run sanitizer selection


class BlobGenAgentOutputState(TypedDict):
    """Output state for BlobGenAgent."""

    # Results
    payload_dict: Dict[str, BlobGenPayload]  # All payloads generated for this CG
    crashed_blobs: Dict[str, BlobGenPayload]  # Only payloads that caused crashes

    # Status information
    status: Annotated[str, merge_with_update]  # Overall status of processing
    error: Annotated[Dict, merge_with_update]


class BlobGenAgentOverallState(BlobGenAgentInputState, BlobGenAgentOutputState):
    """Overall state for BlobGenAgent."""

    # Added in preprocess
    gc: GlobalContext
    llm: LLM
    iter_cnt: int  # Track number of improvement attempts
    cp_name: str

    current_payload: Annotated[
        BlobGenPayload, merge_with_update
    ]  # Use merge_last_value to handle concurrent updates

    start_time: datetime.datetime
    messages: List[BaseMessage]
