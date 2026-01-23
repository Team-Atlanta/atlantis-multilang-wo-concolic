from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, TypeVar

from loguru import logger
from multilspy import multilspy_types
from pydantic import BaseModel, Field

from mlla.utils.analysis_interest import InterestInfo

from ...codeindexer.parser import CIFunctionRes
from .. import (
    get_function_body,
    instrument_line,
    normalize_func_name,
    normalize_func_name_for_ci,
)
from ..bit import LocationInfo
from ..tracer.model import TracerResult

T = TypeVar("T")


class CalleeRes(BaseModel):
    name: str
    needs_to_analyze: bool = Field(default=True)
    tainted_args: list[int]
    line_range: tuple[
        tuple[int, int], tuple[int, int]
    ]  # 1-indexed, range where the callee is likely to be called
    priority: int = Field(default=100)

    def create_tag(self) -> str:
        return f"{self.name}:{self.line_range}"

    def to_func_info(self) -> FuncInfo:
        return FuncInfo(
            func_location=LocationInfo(
                func_name=self.name,
                file_path=None,
                start_line=-1,
                end_line=-1,
            ),
            need_to_analyze=self.needs_to_analyze,
            tainted_args=self.tainted_args,
        )


class SinkDetectReport(BaseModel):
    sink_analysis_message: str
    is_vulnerable: bool
    sink_line: str
    sink_line_number: int
    sanitizer_candidates: list[str]


class MCGASinkDetectReport(SinkDetectReport):
    callsites: list[CalleeRes]

    def to_sink_detect_report(self) -> SinkDetectReport:
        return SinkDetectReport(
            sink_analysis_message=self.sink_analysis_message,
            is_vulnerable=self.is_vulnerable,
            sink_line=self.sink_line,
            sink_line_number=self.sink_line_number,
            sanitizer_candidates=self.sanitizer_candidates,
        )


class FuncInfo(BaseModel):
    func_location: LocationInfo
    func_signature: Optional[str] = Field(default=None)
    func_body: Optional[str] = Field(default=None)
    children: list[FuncInfo] = Field(default_factory=list)
    need_to_analyze: bool = Field(default=False)
    tainted_args: list[int] = Field(default_factory=list)
    sink_detector_report: Optional[SinkDetectReport] = Field(
        default=None,
        compare=False,
    )
    interest_info: Optional[InterestInfo] = Field(default=None)

    def __hash__(self):
        return hash((self.func_location))

    def __eq__(self, other):
        """Enable equality comparison with == operator."""
        if isinstance(other, FuncInfo):
            # Must match name, file path, and start line
            func_name = normalize_func_name_for_ci(self.func_location.func_name)
            func_path = self.func_location.file_path
            func_start = self.func_location.start_line
            other_name = normalize_func_name_for_ci(other.func_location.func_name)
            other_path = other.func_location.file_path
            other_start = other.func_location.start_line
            return (
                func_name == other_name
                and func_path == other_path
                and func_start == other_start
            )
        return False

    def __contains__(self, item) -> bool:
        """Implement the 'in' operator for FuncInfo (LocationInfo in func_info)."""
        func_name = normalize_func_name_for_ci(self.func_location.func_name)
        func_path = self.func_location.file_path
        func_start = self.func_location.start_line
        func_end = self.func_location.end_line

        if isinstance(item, LocationInfo):
            item_name = normalize_func_name_for_ci(item.func_name)
            item_path = item.file_path
            item_start = item.start_line
            item_end = item.end_line

            # Check if LocationInfo is within this function's range
            return (
                func_name == item_name
                and func_path == item_path
                and func_start <= item_start <= func_end
                and func_start <= item_end <= func_end
            )

        return False

    def create_tag(self, verbose: bool = True) -> str:
        tag = normalize_func_name_for_ci(self.func_location.func_name)

        if self.func_location.file_path:
            tag += f":{self.func_location.file_path}:{self.func_location.start_line}"
        else:
            pass
            # logger.warning(
            #     f"No file path for {self.func_location.func_name} but tag is created"
            # )

        if self.func_body and verbose:
            tag += f":{self.func_body}"

        return tag

    def from_external(self) -> bool:
        if not self.func_location.file_path:
            return True
        return False

    def call_recursive(
        self, func: Callable[[FuncInfo], T], reduce: Optional[Callable[..., T]] = None
    ) -> T:
        if self.children:
            ret = [func(self)]
            for child in self.children:
                try:
                    ret.append(child.call_recursive(func))
                except Exception as e:
                    logger.error(f"Error calling recursive function: {e}")
                    logger.error(f"self: {self}")
                    logger.error(f"self.children: {self.children}")
                    logger.error(f"child.children: {child.children}")
                    continue
            if reduce:
                return reduce(ret)
            else:
                return ret[0]
        else:
            return func(self)

    def pretty_str(self) -> str:
        if self.func_body:
            instrumented_fn_body, _ = instrument_line(
                self.func_body, self.func_location.start_line
            )
            s = (
                f"- func_name: {self.func_location.func_name}\n"
                f"- file_path: {self.func_location.file_path}\n"
                f"- start_line: {self.func_location.start_line}\n"
                f"- end_line: {self.func_location.end_line}\n"
                f"- func_body: ```\n{instrumented_fn_body}\n```\n"
            )
        else:
            s = (
                f"- func_name: {self.func_location.func_name}\n"
                f"- file_path: {self.func_location.file_path}\n"
                f"- start_line: {self.func_location.start_line}\n"
                f"- end_line: {self.func_location.end_line}\n"
            )
        if self.func_signature:
            s += f"- func_signature: {self.func_signature}\n"

        return s

    def format_recursive(self, indent: int = 0) -> str:
        s = "  " * indent + str(self) + "\n"
        s += "  " * indent + "- children:\n"
        for child in self.children:
            s += child.format_recursive(indent + 1)
        return s

    def __str__(self) -> str:
        if self.func_body:
            instrumented_fn_body, _ = instrument_line(
                self.func_body, self.func_location.start_line
            )
            s = (
                f"- func_name: {self.func_location.func_name}\n"
                f"- file_path: {self.func_location.file_path}\n"
                f"- start_line: {self.func_location.start_line}\n"
                f"- end_line: {self.func_location.end_line}\n"
                f"- func_body: ```\n{instrumented_fn_body}\n```\n"
            )
        else:
            s = (
                f"- func_name: {self.func_location.func_name}\n"
                f"- file_path: {self.func_location.file_path}\n"
                f"- start_line: {self.func_location.start_line}\n"
                f"- end_line: {self.func_location.end_line}\n"
            )
        s += f"- func_signature: {self.func_signature}\n"
        s += f"- tainted_args: {self.tainted_args}\n"
        s += f"- need_to_analyze: {self.need_to_analyze}\n"
        s += f"- interest_info: {self.interest_info}\n"
        s += f"- sink_detector_report: {self.sink_detector_report}\n"

        return s

    @staticmethod
    def from_lsp_res(
        lsp_loc_res: multilspy_types.Location,
        lsp_sym_res: multilspy_types.UnifiedSymbolInformation,
    ) -> "FuncInfo":
        """Create FuncInfo from LSP response"""
        detail = lsp_sym_res["detail"]
        func_name = normalize_func_name(lsp_sym_res["name"])
        file_path = lsp_loc_res["absolutePath"]

        func_signature = None
        real_func_name = lsp_sym_res["name"]

        if detail:
            if ":" in detail and "(" in real_func_name:
                ret_type = detail.split(":")[1].strip()
                params = "(" + real_func_name.split("(")[1]
                func_signature = f"{ret_type} {func_name}{params}"
            elif "(" in detail and ")" in detail:
                split_detail = detail.split("(")
                ret_type = split_detail[0]
                params = split_detail[1].rsplit(")", 1)[0]
                func_signature = f"{ret_type} {func_name}({params})"
            elif detail:
                logger.warning(f"LSP detail: {detail}")
                ret_type = detail
                func_signature = f"{ret_type} {real_func_name}()"

        start_line = int(lsp_sym_res["range"]["start"]["line"]) + 1
        end_line = int(lsp_sym_res["range"]["end"]["line"]) + 1

        func_body = get_function_body(
            file_path,
            start_line,
            end_line,
        )

        return FuncInfo(
            func_location=LocationInfo(
                func_name=func_name,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
            ),
            func_body=func_body,
            func_signature=func_signature,
        )

    @staticmethod
    def from_ci_res(ci_res: CIFunctionRes) -> "FuncInfo":
        """Create FuncInfo from code dictionary"""
        func_name = normalize_func_name(ci_res.func_name)
        return FuncInfo(
            func_location=LocationInfo(
                func_name=func_name,
                file_path=ci_res.file_path,
                start_line=ci_res.start_line,
                end_line=ci_res.end_line,
            ),
            func_body=ci_res.func_body,
            func_signature=ci_res.func_name,
        )

    @staticmethod
    def from_joern_method(method: dict, base_path: str = "/src") -> "FuncInfo":
        file_path = base_path + "/" + method["filename"]
        func_body = get_function_body(
            file_path,
            method["lineNumber"],
            method["lineNumberEnd"],
        )

        func_signature: Optional[str] = method["fullName"]

        if func_signature:
            if ":" in func_signature:
                func_name = func_signature.split(":")[0]
                ret_type = func_signature.split(":")[1].split("(")[0]
                params = "(" + func_signature.split(":")[1].split("(")[1]
                func_signature = f"{ret_type} {func_name}{params}"
            else:
                func_name = func_signature
                func_signature = method["signature"]
                if func_signature and "(" in func_signature:
                    ret_type = func_signature.split("(")[0]
                    params = "(" + func_signature.split("(")[1]
                    func_signature = f"{ret_type} {func_name}{params}"

        if func_signature and "<unresolvedSignature>" in func_signature:
            func_signature = None

        return FuncInfo(
            func_location=LocationInfo(
                func_name=method["name"],
                file_path=file_path,
                start_line=method["lineNumber"],
                end_line=method["lineNumberEnd"],
            ),
            func_body=func_body,
            func_signature=func_signature,
        )

    @staticmethod
    def check_and_make_abs_file_path(node: FuncInfo):
        if node.func_location.file_path:
            if not Path(node.func_location.file_path).exists():
                node.func_location.file_path = ""
            else:
                node.func_location.file_path = (
                    Path(node.func_location.file_path).resolve().as_posix()
                )

    @staticmethod
    def fill_children_from_callee_dicts(
        node: FuncInfo, callee_dicts: dict[str, FuncInfo]
    ):
        for child in node.children:
            tag = child.create_tag()
            if tag in callee_dicts:
                child.children = callee_dicts[tag].children


class CG(BaseModel):
    """Call Graph (CG) for a function"""

    name: str = Field(description="Name of the API function")
    path: str = Field(
        description="Path to the source file containing the API function definition"
    )
    root_node: FuncInfo = Field(
        description="Root node of the call graph from entry point to the API function"
    )
    called_external_methods: list[FuncInfo] = Field(
        description=(
            "List of methods called externally that might be relevant in this call"
            " graph"
        ),
        default=[],
    )

    def call_recursive(
        self, func: Callable[[FuncInfo], T], reduce: Optional[Callable[..., T]] = None
    ) -> T:
        return self.root_node.call_recursive(func, reduce)

    def update_with_tracer_result(self, tracer_result: TracerResult) -> None:
        pass
