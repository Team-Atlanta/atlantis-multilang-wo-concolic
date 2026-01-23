import json
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, model_validator

class SrcLocation(BaseModel):
    """Represents a source code location"""
    src_path: str
    line: int
    column: int


class SymCCFailedFunctionHookCall(BaseModel):
    """Represents a failed function hook call"""
    library_name: str
    function_name: str
    reason: str
    src_location: Optional[SrcLocation] = None


class InconsistentValue(BaseModel):
    """Represents an inconsistent coerced value that indicates a symbolic propagation failure"""
    name: str
    src_location: Optional[SrcLocation] = None
    coerced_value_a: str  # Using string representation of Dynamic<\'ctx>
    coerced_value_b: str  # Using string representation of Dynamic<\'ctx>

class Inconsistency(BaseModel):
    hex_input_a: str
    hex_input_b: str
    src_location: Optional[SrcLocation] = None
    inconsistent_values: List[InconsistentValue]
    failed_function_hook_calls: List[SymCCFailedFunctionHookCall]

    @model_validator(mode='before')
    @classmethod
    def fix_field_name(cls, data):
        """Fix inconsistent field name if needed"""
        if isinstance(data, dict) and 'inconsistencices' in data:
            data['inconsistencies'] = data.pop('inconsistencices')
        return data


def parse_inconsistency(json_path: Path) -> Inconsistency:
    """Parse the JSON file containing inconsistent coerced values"""
    with open(json_path, "r") as f:
        data = json.load(f)
    return Inconsistency.model_validate(data)
