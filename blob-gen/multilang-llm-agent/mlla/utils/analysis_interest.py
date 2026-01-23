from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field


class InterestPriority(IntEnum):
    NORMAL = 1
    CONTAIN_DIFF_FUNCTION = 2


class InterestInfo(BaseModel):
    is_interesting: bool
    # forward_tainted: bool
    # backward_tainted: bool
    # current_interest: bool
    diff: Optional[str] = Field(default=None)
