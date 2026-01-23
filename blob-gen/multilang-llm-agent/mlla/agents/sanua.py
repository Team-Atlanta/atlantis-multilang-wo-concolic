from langgraph.graph import MessagesState
from typing_extensions import Annotated, List

from ..modules.sanitizer import Sanitizer
from ..utils.agent import SANUA, BaseAgentTemplate
from ..utils.buginfo import BugInfo
from ..utils.cp import sCP
from ..utils.database import Database
from ..utils.state import merge_with_update


# Input state
class SanUnderstandAgentState(MessagesState):
    cp_path: str


# Output state
class SanUnderstandAgentOutputState(MessagesState):
    cp: Annotated[sCP, merge_with_update]
    sanitizers: List[Sanitizer]
    bug_info: List[BugInfo]
    # CP specific bug info
    cp_bug_info: List[BugInfo]


# Intermediate state
class SanUnderstandOverallState(SanUnderstandAgentState, SanUnderstandAgentOutputState):
    bug_db: Database


class SanUnderstandAgent(BaseAgentTemplate):
    def __init__(self, config):
        ret_dir = config.RESULT_DIR / SANUA
        super().__init__(
            config,
            ret_dir,
            SanUnderstandAgentState,
            SanUnderstandAgentOutputState,
            SanUnderstandOverallState,
        )

        self.builder.add_node("understand", self.understand)

        self.builder.add_edge("preprocess", "understand")
        self.builder.add_edge("understand", "finalize")

    def serialize(self, state) -> str:
        # TODO
        return ""

    def deserialize(self, state, content: str) -> dict:
        # TODO
        return {}

    def preprocess(self, state: SanUnderstandAgentState) -> SanUnderstandOverallState:
        return SanUnderstandOverallState(
            sanitizers=[],
            bug_info=[],
            cp_bug_info=[],
            bug_db=Database(),
        )

    def understand(self, state: SanUnderstandOverallState) -> SanUnderstandOverallState:
        return state

    def finalize(
        self, state: SanUnderstandOverallState
    ) -> SanUnderstandAgentOutputState:
        return SanUnderstandAgentOutputState(
            sanitizers=state["sanitizers"],
            bug_info=state["bug_info"],
            cp_bug_info=state["cp_bug_info"],
        )
