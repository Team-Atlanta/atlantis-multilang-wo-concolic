from .inconsistency import Inconsistency, InconsistentValue, SrcLocation
from .sources import locate_src
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import log10


class SourceView:
    # end inclusive
    def __init__(self, src_path: Path, start_line_num: int, end_line_num: int):
        with open(src_path, "r") as f:
            self.lines = f.readlines()
        self.lines = [self.lines[i] for i in range(start_line_num - 1, end_line_num)]
        self.start_line_num = start_line_num
        self.col_annotations = []
        self.line_annotations = {}

    def annotate_column(self, line_num: int, column: int) -> int:
        try:
            footnode_idx = self.col_annotations.index((line_num, column))
        except ValueError:
            footnode_idx = -1
        if footnode_idx == -1:
            self.col_annotations.append((line_num, column))
            footnode_idx = len(self.col_annotations) - 1
        return footnode_idx

    def annotate_line(self, line_num: int, reason: Optional[str] = None):
        if line_num not in self.line_annotations:
            self.line_annotations[line_num] = reason

    def __str__(self) -> str:
        out = ""
        footnotes: Dict[int, List[Tuple[int, int]]] = dict()
        for i, (l, c) in enumerate(self.col_annotations):
            if l not in footnotes:
                footnotes[l] = [(c, i)]
            else:
                footnotes[l].append((c, i))
        
        line_digits = int(log10(self.start_line_num + len(self.lines))) + 2
        for i, orig_line in enumerate(self.lines):
            line_num = self.start_line_num + i
            if line_num in footnotes:
                line = ""
                cols = {x: y for x, y in footnotes[line_num]}
                for col in range(1, len(orig_line) + 1):
                    ch = orig_line[col - 1]
                    if col in cols:
                        aa = cols[col]
                        line += f"/* [{aa}] */"
                    line += ch
            else:
                line = orig_line
            if line_num in self.line_annotations:
                line = line.rstrip()
                reason = self.line_annotations[line_num]
                out += f"[{line_num:>{line_digits}}]:{line} /* @CALLED {reason} */\n"
            else:
                out += f"[{line_num:>{line_digits}}]:{line}"
        return out


MISSING_SOURCE_ERROR_MSG = "Source code location not available"


def visualize_path_constraint_site(
    pc_src_location: Optional[SrcLocation],
    inconsistent_values: List[InconsistentValue],
    src_base: Path,
) -> str:
    """
    Visualizes the path constraint site for the given source location and inconsistent values.
    """
    if pc_src_location is None:
        return f"{MISSING_SOURCE_ERROR_MSG}: path constraint"
    host_src = locate_src(Path(pc_src_location.src_path), src_base)
    if host_src is None:
        return f"{MISSING_SOURCE_ERROR_MSG}: locate_src"
    sv = SourceView(host_src, pc_src_location.line - 10, pc_src_location.line + 10)
    sv.annotate_line(pc_src_location.line)
    ic_annots = []
    for x in inconsistent_values:
        if x.src_location is not None:
            aa = sv.annotate_column(x.src_location.line, x.src_location.column)
            ic_annots.append(
                (aa, f"input_a: {x.coerced_value_a}, input_b: {x.coerced_value_b}")
            )
    out = str(sv)
    for x in ic_annots:
        out += f"\n// [{x[0]}] {x[1]}"
    return out


def visualize_callsite(src_location: Optional[SrcLocation], src_base: Path, reason: str) -> str:
    if src_location is None:
        return f"{MISSING_SOURCE_ERROR_MSG}: callsite"

    host_src = locate_src(Path(src_location.src_path), src_base)
    if host_src is None:
        return f"{MISSING_SOURCE_ERROR_MSG}: locate_src"

    sv = SourceView(host_src, src_location.line - 5, src_location.line + 5)
    sv.annotate_line(src_location.line, reason)
    out = str(sv)
    return out


INDENT = "  "


def construct_user_prompt(inconsistency: Inconsistency, src_base: Path, previous_code: str) -> str:

    out = ""
    # Add path constraint site visualization
    pc_site = visualize_path_constraint_site(
        inconsistency.src_location,
        inconsistency.inconsistent_values,
        src_base,
    )
    out += f"<PATH CONSTRAINT SITE>\n{pc_site}\n</PATH CONSTRAINT SITE>\n"
    out += "\n\n"
    # Add function calls
    out += f"<FUNCTION CALLS>\n"
    for i, fc in enumerate(inconsistency.failed_function_hook_calls):
        out += f"<FUNCTION CALL {i}>\n"
        out += f"<CALLSITE>\n{visualize_callsite(fc.src_location, src_base, fc.reason)}\n</CALLSITE>\n"
    out += f"</FUNCTION CALLS>\n"

    out += "<PREVIOUS CODE>\n"
    out += f"{previous_code}\n"
    out += "</PREVIOUS CODE>\n"

    return out
