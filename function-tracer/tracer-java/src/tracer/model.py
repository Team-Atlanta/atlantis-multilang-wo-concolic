from pydantic import BaseModel
from typing import TypeAlias


class MethodInfo(BaseModel):
    file: str
    prototype: str
    class_name: str
    method_name: str

    def __hash__(self) -> int:
        return hash((self.file, self.prototype, self.class_name, self.method_name))

    def __eq__(self, other) -> bool:
        if not isinstance(other, MethodInfo):
            return False
        return (self.file == other.file and 
                self.prototype == other.prototype and 
                self.class_name == other.class_name and 
                self.method_name == other.method_name)


Caller: TypeAlias = MethodInfo
Callee: TypeAlias = MethodInfo


class CallState(BaseModel):
    file: str
    line: int
    callee: Callee

    def __hash__(self) -> int:
        return hash((self.file, self.line, self.callee))

    def __eq__(self, other) -> bool:
        if not isinstance(other, CallState):
            return False
        return (self.file == other.file and 
                self.line == other.line and 
                self.callee == other.callee)


class Relation(BaseModel):
    caller: Caller
    callees: list[CallState]


Relations: TypeAlias = list[Relation]
