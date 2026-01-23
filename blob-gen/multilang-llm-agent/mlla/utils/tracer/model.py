from pathlib import Path
from typing import Optional, TypeAlias

from pydantic import BaseModel


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
        return (
            self.file == other.file
            and self.prototype == other.prototype
            and self.class_name == other.class_name
            and self.method_name == other.method_name
        )

    def __str__(self) -> str:
        return f"{self.prototype} {self.class_name}.{self.method_name} [{self.file}]"


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

    def __str__(self) -> str:
        return f"{self.function_name} [{self.file}:{self.line}]"


Caller: TypeAlias = FunctionInfo | MethodInfo
Callee: TypeAlias = FunctionInfo | MethodInfo


class CallState(BaseModel):
    file: str
    line: int
    callee: Callee

    def __hash__(self) -> int:
        return hash(self.callee)

    def __eq__(self, other) -> bool:
        if not isinstance(other, CallState):
            return False
        return (
            self.file == other.file
            and self.line == other.line
            and self.callee == other.callee
        )

    def __str__(self) -> str:
        return f"({self.file}:{self.line}) {self.callee}"


class Relation(BaseModel):
    caller: Caller
    callees: list[CallState]

    def __hash__(self) -> int:
        return hash((self.caller, tuple(self.callees)))

    def __str__(self) -> str:
        res = f"{self.caller} -> \n"
        for callee in self.callees:
            res += f"  - {callee}\n"
        return res


class RelationsList(list[Relation]):
    def filter_only_in_project(self, files: list[Path]) -> "RelationsList":
        """Return a new RelationsList that contains only the relations where
        all callers and callees are in the given files."""

        def _check_and_update_to_abs(
            callable: Caller, files: list[Path]
        ) -> Optional[Path]:
            callable_file_name = Path(callable.file).name
            for f in files:
                if callable_file_name == f.name:
                    if _check_classname(callable, f):
                        callable.file = f.as_posix()
                        return f
            return None

        def _check_classname(caller: Caller, f: Path) -> bool:
            if isinstance(caller, MethodInfo):
                class_names = caller.class_name.split(".")
                filtered_class_names = []
                for class_name in class_names:
                    if "$" in class_name:
                        filtered_class_names.append(class_name.split("$")[0])
                    else:
                        filtered_class_names.append(class_name)
                class_name = "/".join(filtered_class_names)
                return class_name in f.as_posix()
            return True

        # file_names = set(f.name for f in files)
        filtered_relations = []
        for rel in self:
            if (caller_file := _check_and_update_to_abs(rel.caller, files)) is not None:
                for cs in rel.callees:
                    cs.file = caller_file.as_posix()
                    _check_and_update_to_abs(cs.callee, files)
                filtered_relations.append(rel)
        return RelationsList(filtered_relations)

    def get_call_graph(self, files: list[Path]) -> dict[Caller, set[CallState]]:
        """Return a dictionary where keys are callers and values are sets of
        callees they call."""
        graph: dict[Caller, set[CallState]] = {}
        for rel in self.filter_only_in_project(files):
            if rel.caller not in graph:
                graph[rel.caller] = set()
            graph[rel.caller].update(rel.callees)
        return graph

    def merge(self, other: "RelationsList") -> "RelationsList":
        """Merge with another RelationsList."""
        return RelationsList(self + other)

    def __str__(self) -> str:
        return "\n".join(str(rel) for rel in self)


class TracerResult:
    def __init__(self, relations: RelationsList, files: list[Path]):
        self._relations = relations
        self._call_graph = self.relations.get_call_graph(files)

    def __str__(self) -> str:
        return f"Relations: {self.relations}\nCall graph: {self.call_graph}"

    @property
    def relations(self) -> RelationsList:
        return self._relations

    @property
    def call_graph(self) -> dict[Caller, set[CallState]]:
        return self._call_graph

    def check_all_callees_in_the_range(
        self,
        callees_set: set[CallState],
        fn_loc: tuple[int, int],
    ) -> bool:
        for callstate in callees_set:
            if callstate.line < fn_loc[0] or callstate.line > fn_loc[1]:
                return False
        return True

    def filter_callees_in_the_range(
        self, callees_set: set[CallState], fn_loc: tuple[int, int]
    ) -> set[CallState]:
        return set(
            [
                callstate
                for callstate in callees_set
                if callstate.line >= fn_loc[0] and callstate.line <= fn_loc[1]
            ]
        )

    def find_callees_by_caller_name_and_path(
        self,
        fn_name: str,
        file_path: str,
        fn_loc: tuple[int, int],
    ) -> set[CallState]:
        for caller, callees_set in self.call_graph.items():
            if (
                isinstance(caller, FunctionInfo)
                and caller.function_name == fn_name
                # and caller.file == file_path
            ):
                # I saw some cases where the callees are not in the range,
                # probably because the callees are in the optimized function
                # which is another callee of the caller,
                # so we need to filter them out.
                return self.filter_callees_in_the_range(callees_set, fn_loc)
            elif (
                isinstance(caller, MethodInfo)
                and caller.method_name == fn_name
                and caller.file == file_path
                and self.check_all_callees_in_the_range(callees_set, fn_loc)
            ):
                return callees_set
        return set()
