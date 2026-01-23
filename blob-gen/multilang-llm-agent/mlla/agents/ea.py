from typing_extensions import TypedDict

from ..utils.agent import EA, BaseAgentTemplate


class ExecuteAgentState(TypedDict):
    pass


class ExecuteAgentOutputState(TypedDict):
    pass


class ExecuteAgent(BaseAgentTemplate):
    def __init__(self, config):
        ret_dir = config.RESULT_DIR / EA
        super().__init__(config, ret_dir, ExecuteAgentState, ExecuteAgentOutputState)
