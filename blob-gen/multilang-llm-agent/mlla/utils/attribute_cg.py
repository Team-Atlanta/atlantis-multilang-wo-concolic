from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union

from loguru import logger
from pydantic import Field

from ..codeindexer.main import CodeIndexer
from ..utils import normalize_func_name
from ..utils.bit import BugInducingThing

# from ..utils.code_tags import END_FUNCTION_TAG, FUNCTION_TAG
from .cg import CG, FuncInfo, LocationInfo
from .code_tags import END_SOURCE_TAG, SOURCE_TAG


def get_coverage_prompt(
    coverage_info: Dict, func_list: List = [], language: str = "jvm"
):
    msg = []
    msg.append("Your previous code touched these lines:")
    msg.append("<COVERAGE_INFO>")

    func_set = set(func_list)

    coverage_by_file: Dict = {}
    for func_sig, info in coverage_info.items():
        func_sig = normalize_func_name(func_sig)
        if func_set and func_sig not in func_set:
            continue

        file_path = info["src"]
        key = (file_path, func_sig)
        if key not in coverage_by_file:
            coverage_by_file[key] = set()

        coverage_by_file[key].update(info.get("lines", []))

    for key, cov_lines in coverage_by_file.items():
        file_path, func_name = key
        with open(file_path, "r") as f:
            lines = f.read().splitlines()

        # msg.append(f"// in file name: {file_path}")
        msg.append(f"- {func_name}")

        for line in sorted(cov_lines):
            line_str = f"{line}: {lines[line-1]}"
            # line_str = f"{line}: {lines[line-1]}\n"
            # line_str = f"{line}: {lines[line-1]} // <--- @VISITED\n"
            msg.append(line_str)
        msg.append("\n")

    msg.append("</COVERAGE_INFO>\n")

    return "\n".join(msg)


def get_bug_prompt(bit: BugInducingThing):
    msg = []

    if not bit.func_location.file_path:
        return ""

    with open(bit.func_location.file_path, "r") as f:
        lines = f.read().splitlines()

    msg.append("You MUST trigger and exploit this vulnerability:")
    msg.append("<VULNERABILITY>")
    msg.append(f"- {bit.func_location.func_name}")
    for line in range(bit.func_location.start_line, bit.func_location.end_line + 1):
        msg.append(f"{line}: {lines[line-1]}")
        # msg.append(f"{line}: {lines[line-1]} // <--- @VULNERABLE\n")
    msg.append("</VULNERABILITY>\n")
    return "\n".join(msg)


def get_key_condition_prompt(bit: BugInducingThing):
    msg = []
    msg.append("You need to consider these key conditions:")
    msg.append("<KEY_CONDITION>")
    key_cond: Dict = {}
    for key_condition in bit.key_conditions:
        if not key_condition.file_path:
            continue

        key = (key_condition.file_path, key_condition.func_name)
        if key not in key_cond:
            key_cond[key] = []

        key_cond[key].append(key_condition)

    for key, key_conditions in key_cond.items():
        func_path, func_name = key
        msg.append(f"- {func_name}")

        with open(func_path, "r") as f:
            lines = f.read().splitlines()

        for key_condition in sorted(key_conditions, key=lambda x: x.start_line):
            for line in range(key_condition.start_line, key_condition.end_line + 1):
                msg.append(f"{line}: {lines[line-1]}")
                # msg.append(f"{line}: {lines[line-1]}\n")
                # msg.append(f"{line}: {lines[line-1]} // <--- @KEY_CONDITION\n")
        msg.append("\n")

    msg.append("</KEY_CONDITION>\n")

    return "\n".join(msg)


def get_analysis_report(bit: BugInducingThing):
    msg = []
    msg.append("This is the vulnerability analysis report from an expert:")
    msg.append("<VULNERABILITY_ANALYSIS_REPORT>")
    for analysis_msg in bit.analysis_message:
        msg.append("<TARGET_VULNERABILITY>")
        msg.append(analysis_msg.sanitizer_type.strip())
        msg.append("</TARGET_VULNERABILITY>")
        msg.append("\n")
        msg.append("<SINK_FUNCTION_DETECTION>")
        msg.append(analysis_msg.sink_detection.strip())
        msg.append("</SINK_FUNCTION_DETECTION>")
        msg.append("\n")
        msg.append("<VULNERABILITY_CLASSIFICATION>")
        msg.append(analysis_msg.vulnerability_classification.strip())
        msg.append("</VULNERABILITY_CLASSIFICATION>")
        msg.append("\n")
        msg.append("<KEY_CONDITIONS_ANALYSIS>")
        msg.append(analysis_msg.key_conditions_report.strip())
        msg.append("</KEY_CONDITIONS_ANALYSIS>")
    msg.append("</VULNERABILITY_ANALYSIS_REPORT>")

    return "\n".join(msg)


def add_line_number(func_body: str, start_number: int = 1) -> Tuple[str, int]:
    """Add line numbers to code for better analysis"""
    lines = func_body.splitlines()
    new_lines = []
    for i, line in enumerate(lines, start_number):
        new_lines.append(f"{i}: {line}")
    return "\n".join(new_lines), i


def find_matching_bit(
    cg: CG, bits: List[BugInducingThing]
) -> Optional[BugInducingThing]:
    """Find a BIT that matches a function in the given CG."""

    def traverse(node: FuncInfo) -> Optional[BugInducingThing]:
        """Traverse CG to find matching BIT."""
        if not node or not node.func_location.func_name:
            return None

        # Check if current node matches any BIT
        for bit in bits:
            if bit.func_location in node:
                return bit

        # Recursively check children
        for child in node.children:
            if match := traverse(child):
                return match

        return None

    return traverse(cg.root_node)


def get_transition_key(src_func: AttributeFuncInfo, dst_func: AttributeFuncInfo) -> str:
    """Generate a unique key for a transition."""
    src_key = (
        f"{src_func.func_location.func_name}_"
        f"{src_func.func_location.file_path}_"
        f"{src_func.func_location.start_line}_"
        f"{src_func.func_location.end_line}"
    )
    dst_key = (
        f"{dst_func.func_location.func_name}_"
        f"{dst_func.func_location.file_path}_"
        f"{dst_func.func_location.start_line}_"
        f"{dst_func.func_location.end_line}"
    )
    return f"{src_key}_to_{dst_key}"


@dataclass
class AnnotationOptions:
    """Options for controlling which annotations appear in the output."""

    show_coverage: bool = False
    show_bug_location: bool = False
    show_key_conditions: bool = False
    show_should_be_taken_lines: bool = False
    show_metadata: bool = False
    from_leaf: bool = False
    show_line_numbers: bool = False  # Show line numbers
    show_only_annotated_lines: bool = False  # Show only lines with annotations
    annotate_unvisited_mark: bool = False  # Add @UNVISITED to lines not visited
    show_func_call_flow: bool = True  # Show function call flow in output
    show_func_list: bool = False  # Show function list in output
    annotation_placement: str = (
        "end"  # Where to place annotations: "before", "end", or "newline"
    )


class AttributeFuncInfo(FuncInfo):
    coverage_info: Dict = Field(default_factory=dict)
    visited_lines: List[int] = Field(default_factory=list)
    total_lines: int = Field(default=0)
    bit_info: Optional[BugInducingThing] = Field(default=None)
    key_conditions: list[LocationInfo] = Field(default_factory=list)
    should_be_taken_lines: list[LocationInfo] = Field(default_factory=list)
    children: list[AttributeFuncInfo] = Field(default_factory=list)

    @classmethod
    def from_func_info(
        cls, func_info: FuncInfo, children: list[AttributeFuncInfo] = []
    ):
        """Create an AttributeFuncInfo instance from a FuncInfo instance."""
        func_start = func_info.func_location.start_line
        func_end = func_info.func_location.end_line
        total_lines = func_end - func_start + 1

        return cls(
            func_location=func_info.func_location,
            func_body=func_info.func_body,
            need_to_analyze=func_info.need_to_analyze,
            tainted_args=func_info.tainted_args,
            sink_detector_report=func_info.sink_detector_report,
            interest_info=func_info.interest_info,
            total_lines=total_lines,
            children=children,
        )

    def get_function_body(self) -> Optional[str]:
        """Get the raw function body without any annotations."""
        if not self.func_body:
            return None
        return self.func_body

    def get_annotated_body(
        self, options: Optional[AnnotationOptions] = None
    ) -> Optional[str]:
        """Get the function body with specified annotations."""
        if not self.func_body:
            return None

        # Use default options if none provided
        if options is None:
            options = AnnotationOptions()

        # Create a mapping of line numbers to their annotations
        line_annotations: Dict[int, Set[str]] = {}

        if options.show_bug_location and self.bit_info:
            # Add bug location annotation
            logger.debug(
                f"Bug lines for {self.func_location.func_name}:"
                f" {self.bit_info.func_location.start_line}-"
                f"{self.bit_info.func_location.end_line}"
            )
            bug_lines = list(
                range(
                    self.bit_info.func_location.start_line,
                    self.bit_info.func_location.end_line + 1,
                )
            )
            for line in bug_lines:
                if line not in line_annotations:
                    line_annotations[line] = set()
                line_annotations[line].add("@BUG_HERE")

        # Add key conditions annotations independently
        if options.show_key_conditions and self.key_conditions:
            for condition in self.key_conditions:
                # logger.debug(
                #     f"Key condition lines for {self.func_location.func_name}:"
                #     f" {condition.start_line}-{condition.end_line}"
                # )
                condition_lines = list(
                    range(condition.start_line, condition.end_line + 1)
                )
                for line in condition_lines:
                    if line not in line_annotations:
                        line_annotations[line] = set()
                    line_annotations[line].add("@KEY_CONDITION")

        # Add should be taken lines annotations independently
        if options.show_should_be_taken_lines and self.should_be_taken_lines:
            for line_info in self.should_be_taken_lines:
                # logger.debug(
                #     f"Should be taken lines for {self.func_location.func_name}:"
                #     f" {line_info.start_line}-{line_info.end_line}"
                # )
                taken_lines = list(range(line_info.start_line, line_info.end_line + 1))
                for line in taken_lines:
                    if line not in line_annotations:
                        line_annotations[line] = set()
                    line_annotations[line].add("@SHOULD_BE_TAKEN")

        # Collect all annotations first
        if options.show_coverage:
            if options.show_only_annotated_lines:
                visited_set = set(self.visited_lines)
                for line_num in line_annotations:
                    if line_num in visited_set:
                        line_annotations[line].add("@VISITED")
                    else:
                        if options.annotate_unvisited_mark:
                            line_annotations[line_num].add("@UNVISITED")
            else:
                for line in self.visited_lines:
                    if line not in line_annotations:
                        line_annotations[line] = set()
                    line_annotations[line].add("@VISITED")

        # Process the function body
        func_body = self.func_body
        lines = func_body.split("\n")
        processed_lines = []
        current_line = self.func_location.start_line

        # Process each line
        for i, line_str in enumerate(lines):
            # Get indentation and prepare annotation if present
            indentation = len(line_str) - len(line_str.lstrip())
            abs_line = current_line + i
            annotation = None
            if line_annotations and abs_line in line_annotations:
                annotation = (
                    "/* " + " | ".join(sorted(line_annotations[abs_line])) + " */"
                )

            # Skip non-annotated lines if show_only_annotated_lines is True
            if options.show_only_annotated_lines and not annotation:
                continue

            # Handle line based on annotation placement and line numbers
            # Add line number format '[' and ']' based on
            # https://www.microsoft.com/en-us/research/wp-content/uploads/2024/08/paper.pdf
            if annotation:
                if options.annotation_placement == "end":
                    if options.show_line_numbers:
                        processed_lines.append(
                            f"[{current_line + i}]: {line_str} {annotation}"
                        )
                    else:
                        processed_lines.append(f"{line_str} {annotation}")
                else:  # before
                    if options.show_line_numbers:
                        indentation += len(f"[{current_line + i}]: ")
                        processed_lines.append(
                            " " * indentation + annotation
                        )  # Annotation line without number
                        processed_lines.append(
                            f"[{current_line + i}]: {line_str}"
                        )  # Numbered code line
                    else:
                        processed_lines.append(
                            " " * indentation + annotation
                        )  # Annotation line without number
                        processed_lines.append(line_str)
            else:
                if options.show_line_numbers:
                    processed_lines.append(f"[{current_line + i}]: {line_str}")
                else:
                    processed_lines.append(line_str)

        func_body = "\n".join(processed_lines)

        if options.show_metadata:
            # TODO: DK: This seems not really helpful. We need to test more.
            metadata: List[str] = [
                # f"// @File: {self.func_location.file_path}",
                # f"// @Function: {self.func_location.func_name}",
            ]
            if self.bit_info:
                for msg in self.bit_info.analysis_message:
                    metadata.extend(
                        [
                            # f"/* @Sink Detection: {msg.sink_detection} */",
                            # f"/* @BUG: {msg.vulnerability_classification} */",
                            # f"/* @Sanitizer Type: {msg.sanitizer_type} */",
                            f"/* Key condition report: {msg.key_conditions_report} */",
                        ]
                    )
            if metadata:
                func_body = "\n".join(metadata) + f"\n{func_body}"

        return func_body


class AttributeCG(CG):
    root_node: AttributeFuncInfo = Field()
    bit_node: Optional[AttributeFuncInfo] = Field(default=None)
    language: str = Field(default="jvm")
    called_external_methods: list[FuncInfo] = Field(default=[])
    coverage_info: Dict = Field(default={})
    focus_on_bit: bool = Field(default=False)  # Default to False for tests
    bit_functions: Set[str] = Field(default_factory=set)

    def __hash__(self):
        """Make AttributeCG hashable by using its name, path, and transitions."""
        hash_components = []

        # Add transitions to make the hash more unique
        transitions = self.find_unique_transitions()
        for src, dst in transitions:
            transition_key = get_transition_key(src, dst)
            hash_components.append(transition_key)

        # Create a tuple of all components and hash it
        return hash(tuple(hash_components))

    def find_unique_transitions(
        self,
    ) -> List[Tuple[AttributeFuncInfo, AttributeFuncInfo]]:
        """Find all unique transitions in the call graph."""
        if not self.root_node:
            logger.error("No root node in AttributeCG")
            return []

        # Find all possible transitions in the call graph
        transitions = []
        # Use a set to track unique transitions to avoid duplicates
        unique_transitions = set()

        # Helper function to traverse the call graph and find transitions
        def traverse(node):
            # For each child, create a transition from current node to child
            for child in node.children:
                # Create a unique key for this transition
                transition_key = get_transition_key(node, child)

                # Only add the transition if we haven't seen it before
                if transition_key not in unique_transitions:
                    unique_transitions.add(transition_key)
                    transitions.append((node, child))

                # Continue traversing
                traverse(child)

        # Start traversal from root node
        traverse(self.root_node)

        if not transitions:
            logger.warning("No transitions found in the call graph")

        logger.debug(f"Found {len(transitions)} unique transitions in call graph")
        return transitions

    @staticmethod
    def _update_node_coverage(
        node: AttributeFuncInfo, coverage_info: Dict, language: str = "jvm"
    ) -> None:
        """Update coverage information for a single node."""
        node_coverage = {}
        node_visited_lines = []

        # Normalize the node's function name for matching
        func_name = normalize_func_name(node.func_location.func_name)
        func_path = node.func_location.file_path
        func_start = node.func_location.start_line
        func_end = node.func_location.end_line

        for cov_func_sig, info in coverage_info.items():
            # Normalize the function signature for matching
            cov_func_name = normalize_func_name(cov_func_sig)
            cov_func_path = info["src"]

            # Match using normalized names and file path comparison
            if func_name == cov_func_name and func_path == cov_func_path:
                coverage_lines = info.get("lines", [])
                if not coverage_lines:
                    continue

                # Filter out lines that are outside the function's range
                valid_lines = []
                for line in coverage_lines:
                    if func_start <= line <= func_end:
                        valid_lines.append(line)

                if valid_lines:
                    node_coverage = info
                    node_visited_lines = sorted(valid_lines)
                    break

        node.coverage_info = node_coverage
        node.visited_lines = node_visited_lines
        logger.debug(
            f"Updated coverage for {node.func_location.func_name}: {node_visited_lines}"
        )

    @staticmethod
    def reset_coverage(node: AttributeFuncInfo):
        """Reset coverage information for a node and all its children."""
        node.coverage_info = {}
        node.visited_lines = []
        for child in node.children:
            AttributeCG.reset_coverage(child)

    def update_coverage(self, coverage_info: Dict) -> None:
        """Update coverage information for all nodes in the CG."""
        self.coverage_info = coverage_info

        # Reset all nodes' coverage information
        AttributeCG.reset_coverage(self.root_node)

        if not coverage_info:
            return

        # Update with new coverage information
        def traverse(node: AttributeFuncInfo):
            self._update_node_coverage(node, coverage_info, self.language)
            for child in node.children:
                traverse(child)

        traverse(self.root_node)

    def get_function_bodies(self) -> List[str]:
        """Get all function bodies without annotations."""
        function_bodies = []
        visited_functions = set()  # Track visited functions by their signature

        def traverse(node: AttributeFuncInfo):
            func_sig = AttributeCG.get_func_signature(node)
            if func_sig in visited_functions:
                return

            visited_functions.add(func_sig)

            # Skip if focusing on BIT and this node is not related to BIT
            if not self.focus_on_bit or func_sig in self.bit_functions:
                if body := node.get_function_body():
                    function_bodies.append(body)

            for child in node.children:
                traverse(child)

        traverse(self.root_node)
        return function_bodies

    @staticmethod
    def get_func_signature(node: Union[AttributeFuncInfo, BugInducingThing]) -> str:
        """Get a unique signature for a function based on name and file path."""
        func_name = node.func_location.func_name
        func_path = node.func_location.file_path
        func_start = node.func_location.start_line

        return f"{func_name}:{func_path}:{func_start}"

    def get_annotated_function_bodies(
        self,
        options: Optional[AnnotationOptions] = None,
        target_functions: List[AttributeFuncInfo] = [],
    ) -> str:
        """Get all function bodies with specified annotations."""
        function_bodies = []
        visited_functions = set()  # Track visited functions by their signature
        target_func_set = {
            AttributeCG.get_func_signature(func) for func in target_functions
        }

        def traverse(node: AttributeFuncInfo):
            func_sig = AttributeCG.get_func_signature(node)
            if func_sig in visited_functions:
                return

            # Check if all target functions are covered
            if target_functions and all(
                func in visited_functions for func in target_func_set
            ):
                return

            visited_functions.add(func_sig)

            # Only include nodes that match our filtering criteria
            if not self.focus_on_bit or func_sig in self.bit_functions:
                if body := node.get_annotated_body(options):
                    func_name = node.func_location.func_name
                    file_path = node.func_location.file_path
                    if options and options.show_bug_location and node == self.bit_node:
                        target_tag = "VULNERABLE_FUNCTION"

                    elif node == self.root_node:
                        target_tag = "ENTRY_FUNCTION"

                    else:
                        target_tag = "FUNCTION"

                    ret_str = f"<{target_tag}>\n"
                    ret_str += f"<FILE_PATH>{file_path}</FILE_PATH>\n"
                    ret_str += f"<FUNC_NAME>{func_name}</FUNC_NAME>\n"
                    ret_str += "<FUNC_BODY>\n"
                    ret_str += f"{body}\n"
                    ret_str += "</FUNC_BODY>\n"
                    ret_str += f"</{target_tag}>\n"

                    function_bodies.append(ret_str)

            # Continue traversing children
            for child in node.children:
                traverse(child)

        traverse(self.root_node)

        added_count = 0
        logger.info(
            f"called_external_methods length: {len(self.called_external_methods)}"
        )
        for relevant_function in self.called_external_methods:
            func_sig = AttributeCG.get_func_signature(relevant_function)
            if func_sig in visited_functions:
                logger.info(f"external: skip {func_sig} because already visited")
                continue

            # TODO: fetch function body using codeindexer?
            if not relevant_function.func_body:
                logger.info(f"external: skip {func_sig} because no body")
                continue

            added_count += 1
            visited_functions.add(func_sig)

            func_name = relevant_function.func_location.func_name
            file_path = relevant_function.func_location.file_path
            if not file_path:
                file_path = "This is an external function with an unknown path"

            # TODO: get_annotated_body of external function
            func_body = relevant_function.func_body

            ret_str = "<FUNCTION>\n"
            ret_str += f"<FILE_PATH>{file_path}</FILE_PATH>\n"
            ret_str += f"<FUNC_NAME>{func_name}</FUNC_NAME>\n"
            ret_str += "<FUNC_BODY>\n"
            ret_str += f"{func_body}\n"
            ret_str += "</FUNC_BODY>\n"
            ret_str += "</FUNCTION>\n"

            function_bodies.append(ret_str)

        logger.info(f"Added {added_count} external functions")

        # Make the leaf nodes to the first (buggy point is more important)
        if options and options.from_leaf:
            function_bodies.reverse()

        # Determine the header text based on options
        if options and options.show_func_list and options.show_func_call_flow:
            header_text = (
                "Below are function lists, function call flow, and their source code:"
            )
        elif options and options.show_func_list:
            header_text = "Below are function lists and their source code:"
        elif options and options.show_func_call_flow:
            header_text = "Below is function call flow and its source code:"
        else:
            header_text = "Below is source code:"

        # Prepare the parts to include
        parts = []

        # Add function list if requested
        if options and options.show_func_list:
            func_list = self.get_func_list()
            func_list = list(map(lambda x: f"- {x}", func_list))
            func_list_str = "<FUNCTION_LIST>\n"
            func_list_str += "\n".join(func_list).strip() + "\n"
            func_list_str += "</FUNCTION_LIST>\n"
            parts.append(func_list_str)

        # Add function call flow if requested
        if options and options.show_func_call_flow:
            func_call_flow_str = self.get_call_flow()
            parts.append(func_call_flow_str)

        # Assemble the final output
        function_bodies_str = f"{header_text}\n"
        function_bodies_str += f"{SOURCE_TAG}\n"

        if parts:
            function_bodies_str += "\n".join(parts).strip() + "\n\n"

        function_bodies_str += "\n".join(function_bodies).strip()
        function_bodies_str += f"\n{END_SOURCE_TAG}\n"

        logger.debug(f"Annotated function body:\n{function_bodies_str}")

        return function_bodies_str

    def get_func_list(self, language: str = "jvm") -> List[str]:
        """Format control flow information showing paths to buggy functions."""
        func_list = []
        visited_funcs = set()  # To avoid duplicates while preserving order

        def traverse(node: AttributeFuncInfo):
            # Skip if focusing on BIT and this node is not related to BIT
            func_sig = AttributeCG.get_func_signature(node)

            if func_sig in visited_funcs:
                return

            visited_funcs.add(func_sig)

            if not self.focus_on_bit or func_sig in self.bit_functions:
                # Add this function to the list (normalize once)
                normalized_name = normalize_func_name(node.func_location.func_name)
                func_list.append(normalized_name)

            # Continue traversing children
            for child in node.children:
                traverse(child)

        traverse(self.root_node)
        return func_list  # Preserve the order of visited functions

    def get_call_flow(self) -> str:
        """Format control flow information showing paths to buggy functions."""
        flow_lines = []

        def traverse(node: AttributeFuncInfo, depth: int) -> None:
            # Only include nodes that match our filtering criteria
            func_sig = AttributeCG.get_func_signature(node)

            if not self.focus_on_bit or func_sig in self.bit_functions:
                indent = "  " * depth
                if (
                    node.bit_info
                ):  # or node.key_conditions or node.should_be_taken_lines:
                    flow_lines.append(f"{indent}  // @BUG is in the below function.")
                func_name = node.func_location.func_name
                func_name = " ".join(map(lambda x: x.strip(), func_name.split("\n")))
                func_name = func_name.split("(")[0]
                flow_lines.append(f"{indent}↳ {func_name}")

            # Continue traversing children
            for child in node.children:
                traverse(child, depth + 1)

        traverse(self.root_node, 0)

        flow_lines_str = "<FUNCTION_CALL_FLOW>\n"
        flow_lines_str += "\n".join(flow_lines) + "\n"
        flow_lines_str += "</FUNCTION_CALL_FLOW>\n"

        # logger.debug(f"Annotated call flow:\n{flow_lines_str}")

        return flow_lines_str

    @classmethod
    def from_cg(
        cls,
        cg: CG,
        code_indexer: CodeIndexer,
        coverage_info: Optional[Dict] = None,
        bit: Optional[BugInducingThing] = None,
        focus_on_bit: bool = False,  # Default to False for tests
        language: str = "jvm",
    ) -> AttributeCG:

        # Initialize bit_functions set
        bit_functions = set()

        # If we have a BIT, add all related functions to bit_functions
        if bit:
            # Add analyzed functions from BIT
            for func in bit.analyzed_functions:
                bit_functions.add(AttributeCG.get_func_signature(func))

            # We need to find the actual function that contains the BIT
            # This will be done in create_attribute_node when we find a matched node

        def create_attribute_node(func_info: FuncInfo) -> Optional[AttributeFuncInfo]:
            # Skip nodes without function name
            func_name = func_info.func_location.func_name
            func_path = func_info.func_location.file_path

            if not func_name:
                logger.debug(
                    f"Skipping node without func_name: {func_info.func_location}"
                )
                return None

            # For nodes without file_path, we'll still include them but log a warning
            if not func_path:
                logger.debug(
                    "Node without file_path will be included:"
                    f" {func_info.func_location}"
                )

            # Process children, filtering out those without file_path
            valid_children = []
            for child in func_info.children:
                child_node = create_attribute_node(child)
                # Only include children that weren't skipped
                if child_node is not None:
                    valid_children.append(child_node)

            # Create node with valid children
            node = AttributeFuncInfo.from_func_info(func_info, valid_children)
            func_sig = AttributeCG.get_func_signature(node)

            # Update coverage if provided
            if coverage_info:
                AttributeCG._update_node_coverage(node, coverage_info, language)

            # Update bug info and key conditions if applicable
            if bit and bit.func_location.file_path and bit.func_location.func_name:
                # Map BIT if function matches
                if bit.func_location in node:
                    node.bit_info = bit
                    # Self include BIT function.
                    bit_functions.add(func_sig)

                # Map key conditions separately
                for condition in bit.key_conditions:
                    if condition in node:
                        node.key_conditions.append(condition)
                        # If there is missing funcs in BIT's analyzed_functions
                        bit_functions.add(func_sig)

                # Map should be taken lines separately
                for line_info in bit.should_be_taken_lines:
                    if line_info in node:
                        node.should_be_taken_lines.append(line_info)
                        # If there is missing funcs in BIT's analyzed_functions
                        bit_functions.add(func_sig)

            return node

        def find_bit_node(node: AttributeFuncInfo) -> Optional[AttributeFuncInfo]:
            if node.bit_info:
                return node
            for child in node.children:
                if bit_node := find_bit_node(child):
                    return bit_node
            return None

        new_root = create_attribute_node(cg.root_node)

        # Handle case where root node doesn't have file_path
        if new_root is None:
            logger.warning(
                f"Root node for CG {cg.name} doesn't have file_path or func_name"
            )
            # Create a minimal valid root node
            new_root = AttributeFuncInfo(
                func_location=cg.root_node.func_location,
                func_body="",
                sink_detector_report=None,  # Use None instead of empty dict
                total_lines=0,
                children=[],  # No children since we're skipping nodes without file_path
            )
            bit_node = None
        else:
            bit_node = find_bit_node(new_root)

        return cls(
            name=cg.name,
            path=cg.path,
            root_node=new_root,
            bit_node=bit_node,
            language=language,
            called_external_methods=(
                cg.called_external_methods if cg.called_external_methods else []
            ),
            focus_on_bit=focus_on_bit,
            bit_functions=bit_functions,
        )
