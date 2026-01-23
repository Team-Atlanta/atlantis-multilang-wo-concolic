from enum import Enum


class AutoPromptChoice(Enum):
    NOCHANGE = 0
    AUTO = 1
    FEW_SHOTS = 2
    COT = 3


# TODO: Find a proper timeout value
# TODO: Use ""env.shared"
# LLM_TIMEOUT = 120  # 2 minutes
# LLM_MAX_RETRIES = 5
