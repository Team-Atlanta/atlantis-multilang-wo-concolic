from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from mlla.utils.analysis_interest import InterestPriority


class LocationInfo(BaseModel):
    func_name: str
    file_path: Optional[str]
    # 1-indexed
    # -1 means not available
    start_line: int
    # 1-indexed
    # -1 means not available
    end_line: int

    def __hash__(self):
        return hash((self.func_name, self.file_path, self.start_line, self.end_line))

    def __eq__(self, other):
        return (
            self.func_name == other.func_name
            and self.file_path == other.file_path
            and self.start_line == other.start_line
            and self.end_line == other.end_line
        )
        return False


class AnalysisMessages(BaseModel):
    sink_detection: str
    vulnerability_classification: str
    sanitizer_type: str
    key_conditions_report: str


class AnalyzedFunction(BaseModel):
    func_location: LocationInfo
    func_body: str


@dataclass
class BugInducingThing:
    harness_name: str
    func_location: LocationInfo
    key_conditions: list[LocationInfo]
    should_be_taken_lines: list[LocationInfo]
    analysis_message: list[AnalysisMessages]
    analyzed_functions: list[AnalyzedFunction]
    priority: int = InterestPriority.NORMAL

    def to_dict(self):
        return {
            "harness_name": self.harness_name,
            "func_location": self.func_location.model_dump(),
            "key_conditions": [loc.model_dump() for loc in self.key_conditions],
            "should_be_taken_lines": [
                loc.model_dump() for loc in self.should_be_taken_lines
            ],
            "analysis_message": [am.model_dump() for am in self.analysis_message],
            "analyzed_functions": [af.model_dump() for af in self.analyzed_functions],
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            harness_name=data["harness_name"],
            func_location=LocationInfo.model_validate(data["func_location"]),
            key_conditions=[
                LocationInfo.model_validate(loc)
                for loc in data.get("key_conditions", [])
            ],
            should_be_taken_lines=[
                LocationInfo.model_validate(loc)
                for loc in data.get("should_be_taken_lines", [])
            ],
            analysis_message=[
                AnalysisMessages.model_validate(am)
                for am in data.get("analysis_message", [])
            ],
            analyzed_functions=[
                AnalyzedFunction.model_validate(af)
                for af in data.get("analyzed_functions", [])
            ],
            priority=data.get("priority", 1),
        )

    def san_type_to_san_name(self) -> str:
        san_type = self.analysis_message[0].sanitizer_type
        if "." in san_type:
            return san_type.split(".")[0]
        if san_type == "Deserialization":
            return "RemoteCodeExecution"
        if san_type == "FilePathTraversal":
            return "FileSystemTraversal"
        return san_type
