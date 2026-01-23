from pydantic import BaseModel
from typing import TypeAlias


class FunctionInfo(BaseModel):
    file: str
    line: int
    function_name: str

    def __hash__(self) -> int:
        return hash((self.file, self.function_name))

    def __eq__(self, other) -> bool:
        if not isinstance(other, FunctionInfo):
            return False
        return self.file == other.file and self.function_name == other.function_name


Caller: TypeAlias = FunctionInfo
Callee: TypeAlias = FunctionInfo


class CallState(BaseModel):
    file: str
    line: int
    callee: Callee

    def __hash__(self) -> int:
        return hash((self.file, self.line, self.callee))

    def __eq__(self, other) -> bool:
        if not isinstance(other, CallState):
            return False
        return (
            self.file == other.file
            and self.line == other.line
            and self.callee == other.callee
        )


class Relation(BaseModel):
    caller: Caller
    callees: list[CallState]


Relations: TypeAlias = list[Relation]
