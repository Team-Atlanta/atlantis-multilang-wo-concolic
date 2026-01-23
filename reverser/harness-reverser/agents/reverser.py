from pathlib import Path
import asyncio
import inspect
import operator
from collections import defaultdict
from typing import Tuple, List, Set, Optional, Dict
from typing_extensions import Annotated, TypedDict
from functools import partialmethod
from filelock import FileLock
from loguru import logger
import time
from pydantic import BaseModel, Field
import re
import tempfile
import random
import subprocess
import time
import os
import json
import difflib
import traceback

from langgraph.graph import END, START, StateGraph, MessagesState
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage, RemoveMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from langchain_anthropic import ChatAnthropic
import anthropic

from tools.llm import LLM
from tools.state import merge_with_update, merge_dict_with_update, merge_set_with_update
from tools.context import ReverserContext

from agents.code_parser import Code, CodeLocation, CodeTool, LanuageServerArgumentError

# TODO: change old code
from tools.resolve_preprocessor import preprocessor_pass, switch_case_constant_patch

# From customgen
from customgen import fetch_generators

# From rust code
import testlang
from testlang import PyTestLangWarning

from fuzzdb import FuzzDB, CovInfo

EXTRA_HEADERS = "extra_headers"
ANTHROPIC_BETA_HEADER_KEY = "anthropic-beta"
ANTHROPIC_BETA_HEADER_VALUE_EXTENDED_CACHE_TTL = "extended-cache-ttl-2025-04-11"

REASONING_MODEL = "claude-3-7-sonnet-20250219"
CODING_MODEL = "claude-sonnet-4-20250514"
ALL_MODELS = [REASONING_MODEL, CODING_MODEL]
# For sake of TPM, but no meaning for now.
DEFAULT_MODEL = CODING_MODEL

MAX_TOKENS = {
    # OTPM for claude-3-7-sonnet-20250219 is 50000 during the contest
    REASONING_MODEL: 32000,
    CODING_MODEL: 32000,
}

ROOT_DIR = Path(__file__).parent.parent
PROMPT_DIR = ROOT_DIR / 'prompts'
JSON_SCHEMA_TAG_NAME = "JSON_schema"
CUSTOM_TYPE_IDS_TAG_NAME = "custom_type_ids"

CODE_MODEL = "gpt-4.1"
CODE_COLLECT_TRIALS = 10

MAX_TRIALS = 40
NO_WARNINGS_TRIALS = 4
MAX_WARNING_TRIALS = 8

# For structured output
class CodeReference(BaseModel):
    """
    Represents a reference to a code element (function, variable, struct, etc.) to analyze in the next step.

    You may specify the *location where the symbol was used* (referenced).

    When specifying a location, both `file_path` and `line_num` must be provided together.
    """

    name: str = Field(
        ...,
        description=(
            "The name of the code element to analyze (e.g., a function, struct, or variable). "
            "Do NOT put file name or path here."
            "DO NOT include `class_name` or package name here when you search class methods. "
        )
    )

    file_path: Optional[str] = Field(
        None,
        description=(
            "The absolute path to the file in which the code element is referenced. (Always `/src/repo/...` or the harness path in `<harness>`) "
            "Use ONLY the file paths given in the <code-blocks> or <harness>. "
            "DO NOT guess the file name from the code element name. "
            "Must be provided together with `line_num`. "
        )
    )

    line_num: Optional[int] = Field(
        None,
        description=(
            "The line number in the file where the code element is referenced. "
            "Must be provided together with `file_path`."
            "DO NOT guess the line number. Find it from the given code lines. "
        )
    )

    class_name: Optional[str] = Field(
        None,
        description=(
            "Optional class or struct name if the element is part of a class context. "
            "Useful for resolving methods or namespaced elements."
        )
    )

class TestLangRef(BaseModel):
    record_name: str = Field(
        description=(
            "Name of the record in testlang to refer to. "
        )
    )
    field_name: str = Field(
        description=(
            "Name of the field in the record to refer to. "
        )
    )

    def __str__(self):
        return f"{self.record_name}.{self.field_name}"

class FunctionLinesRef(BaseModel):
    """
    Represents a line in a function that the LLM is interested in.
    This is used to guide the code generation process to focus on specific lines.
    """

    file_path: str = Field(
        description=(
            "The absolute path to the file in which the line is located. "
            "Use ONLY the file paths given in the <code-blocks> or <harness>."
        )
    )

    func_name: str = Field(
        description=(
            "The name of the function in which the line is located. "
            "This is used to guide the code generation process to focus on specific functions."
        )
    )

    line_nums: List[int] = Field(
        description=(
            "List of line numbers in the function that the LLM wants to cover to trigger the security vulnerability. "
            "This is used to guide the code generation process to focus on specific lines in functions."
        )
    )

    def to_cov_info(self) -> CovInfo:
        return CovInfo(self.func_name, self.file_path, self.line_nums)

class PythonCode(BaseModel):
    name: str = Field(
        ...,
        description=(
            "Python class name for the `encoder` or `generator` testlang field attribute."
        )
    )
    code: str = Field(
        ...,
        description=(
            "Python code for `encoder` or `generator`."
            "Example for `generator`:"
            "```python"
            "# Try different packages on `ModuleNotFoundError: No module named`"
            "import any_needed_packages"
            ""
            "class ExploitGenerator:"
            "    def generate(self) -> bytes:"
            "        # Generate generate blob PROGRAMMATICALLY to trigger the security vulnerability."
            "        # NEVER manually generate complex file formats like crypto, image, or audio files. (e.g. You can use `cryptography` for crypto.)"
            "        # Use libraries as much as possible."
            "        pass"
            ""
            "    def validate(self, input: bytes):"
            "        # Validate the generated blob so that blob can be parsed and trigger the security vulnerability."
            "        # If harness was explicitly set to ignore some checking, you can skip only that parts of validation, but not all validation."
            "        # NEVER use try-except so that we can see the exceptions for debugging."
            "        # Use libraries as much as possible."
            "        pass"
            "```"
        )
    )
    security_vulnerability_severity_score: int = Field(
        description=(
            "Severity score of the security vulnerability that this code is intended to trigger. (0-100)"
            "For C/C++, `AddressSanitizer` can detect `heap-use-after-free`, `heap-buffer-overflow`, `stack-buffer-overflow`, `global-buffer-overflow`, `stack-use-after-return`, `stack-use-after-scope`, `initialization-order-fiasco`, `detected memory leaks`."
        )
    )
    probability_to_trigger_security_vulnerability_in_percentage: int = Field(
        description=(
            "Probability that this code will trigger the security vulnerability in percentage. (0-100) "
            "For C/C++, `AddressSanitizer` can detect `heap-use-after-free`, `heap-buffer-overflow`, `stack-buffer-overflow`, `global-buffer-overflow`, `stack-use-after-return`, `stack-use-after-scope`, `initialization-order-fiasco`, `detected memory leaks`."
        )
    )
    validate_method_score: int = Field(
        description=(
            "How well the `validate` method relies on libraries to validate the generated blob rather than manually implementing the validation. (0-100)"
        )
    )
    target_testlang_field: TestLangRef = Field(
        ...,
        description=(
            "The testlang field that this code is intended to generate or encode. "
        )
    )
    other_affected_testlang_fields: List[TestLangRef] = Field(
        ...,
        description=(
            "REVIEW `code` and determine if this `code` also generates or encodes other testlang fields too. "
        )
    )

class ReverserLLMOutput(BaseModel):
    """
    Output of a reverse analysis step.

    - chain_of_thought: Reasoning used to interpret the source code and input structure.
    - sub_testlang: Partial testlang representing new or updated structural records.
    - records_to_remove: List of record names to be removed from the testlang.
    - codes_to_analyze: Additional code elements to be analyzed in the next step.
    - suppressed_warnings: Indexes of warnings that should be ignored going forward.
    """

    chain_of_thought: str = Field(
        ...,
        description="Reasoning and observations from analyzing the provided code."
    )

    codes_to_analyze: List[CodeReference] = Field(
        ...,
        description=(
            "List of code elements (e.g., functions, structs, variables) to analyze in the next step. "
        )
    )

    sub_testlang: str = Field(
        ...,
        description=(
            "Partial testlang output containing only new or updated records. "
            "This will be applied as a patch to the full testlang in the next step. "
            "This SHOULD BE a *partial* testlang, containing only new or updated records. "
        )
    )

    python_codes: List[PythonCode] = Field(
        ...,
        description=(
            "List of Python `encoder` or `generator`."
        )
    )

    records_to_remove: Set[str] = Field(
        ...,
        description=(
            "Set of record names to be removed from the testlang in the next step."
        )
    )

    python_codes_to_remove: Set[str] = Field(
        ...,
        description=(
            "Set of python code names to be removed in the next step."
        )
    )

    suppressed_warnings: List[int] = Field(
        ...,
        description=(
            "List of index of warnings that the LLM has decided to ignore."
            "Those warnings will not be included in the next step."
        )
    )

    suppressed_codes: List[int] = Field(
        [],
        description=(
            "List of indices of code blocks shown in <code-blocks> that are no longer needed for future analysis. "
            "Use this to reduce token usage by discarding any code that has already been fully reflected in sub_testlang, "
            "or is no longer relevant to remaining analysis. "
            "Be aggressive in removing unnecessary code to minimize future context size."
        )
    )

    lines_to_cover: List[FunctionLinesRef] = Field(
        description=(
            "List of specific lines in functions that the LLM wants to cover to trigger the security vulnerability. "
            "This is used to guide the code generation process to focus on specific lines in functions."
        )
    )

    record_to_analyze_next: str = Field(
        description=(
            "Name of the record in testlang that the LLM wants to analyze next. "
            "This is used to guide the analysis process to focus on specific records."
        )
    )

    diff: str = Field(
        description=(
            "diff of the changes made to the testlang and python codes in this step. "
            "This should include record and field names and specific codes changes. "
            "This is used to track changes made to the testlang and to visualize the changes in the next step."
            "If there was an error, this should be summarize with the previous diff."
        )
    )

class ReverserAgentState(TypedDict):
    harness_path: Path
    harness_name: str
    diff_path: Optional[Path]

class ReverserAgentOutputState(TypedDict):
    testlang: str
    python_codes: Dict[str, PythonCode]
    count: int

class ReverseOneState(MessagesState):
    testlang_map: Annotated[Dict[int, str], merge_dict_with_update]
    testlang: Annotated[str, merge_with_update]
    tl_valid: Annotated[str, merge_with_update]
    tl_count: Annotated[dict, merge_dict_with_update]
    trials: Annotated[int, merge_with_update]
    warning_trials: Annotated[int, merge_with_update]
    warnings: Annotated[List[str], merge_with_update]
    error: Annotated[str, merge_with_update]
    suppressed_warnings: Annotated[set, merge_set_with_update]
    llm_output: Annotated[ReverserLLMOutput, merge_with_update]
    code_blocks: Annotated[List[Code], merge_with_update]
    python_codes: Annotated[Dict[str, Optional[PythonCode]], merge_with_update]

class ReverserOverallState(ReverseOneState, ReverserAgentState, ReverserAgentOutputState):
    pass

def load_file(fname: Path) -> str:
    with open(fname, "rt", errors="replace") as f: return f.read()

def extract_testlang(s: str) -> str:
    return extract_json(extract_tag(s, "testlang"))

def extract_json(s: str) -> str:
    return extract_str(s, "{", "}", include_tok=True)

def extract_tag(s: str, tag: str) -> str:
    return extract_str(s, f"<{tag}>", f"</{tag}>")

def extract_str(s: str, start_tok: str, end_tok: str, include_tok=False) -> str:
    start = s.find(start_tok)
    if start == -1:
        raise ValueError(f"No start token `{start_tok}` found")
    end = s.rfind(end_tok, start)
    if end == -1:
        raise ValueError(f"No end token `{end_tok}` found")
    if include_tok:
        end = end+len(end_tok)
    else:
        start = start+len(start_tok)
    return s[start:end]

def process_git_diff(diff: str) -> Tuple[str, List[Code]]:
    new_diff = ""
    codes = []

    cur_file_path = None
    cur_code_body = ""
    cur_code_loc = None
    cur_line_num = None
    line_num_width = None
    total_size = 0
    DIFF_MAX_SIZE = 100000

    for line in diff.splitlines():
        if match := re.search(r'^diff --git ', line):
            cur_line_num = None
            line_num_width = None
        elif match := re.search(r'^--- a/(.+)', line):
            pass
        elif match := re.search(r'^\+\+\+ b/(.+)', line):
            cur_file_path = f"/src/repo/{match.group(1)}"
        elif match := re.search(r'^@@ -\d+,\d+ \+(\d+),(\d+) @@', line):
            if cur_code_body:
                if total_size + len(cur_code_body) > DIFF_MAX_SIZE:
                    logger.warning("Skipping code block due to size limit: {} bytes", len(cur_code_body))
                    break
                total_size += len(cur_code_body)
                codes.append(Code(
                    name=f"diff_{len(codes) + 1}",
                    body=cur_code_body,
                    location=cur_code_loc,
                ))

            cur_code_body = ""
            start_line = int(match.group(1))
            end_line = start_line + int(match.group(2)) - 1
            if cur_file_path:
                cur_code_loc = CodeLocation(
                    file_path=cur_file_path,
                    start_line=start_line,
                    end_line=end_line
                )
                cur_line_num = start_line
                line_num_width = len(str(cur_code_loc.end_line))
        elif cur_line_num is not None and line_num_width is not None:
            if line.startswith('-'):
                empty_line_num = " " * line_num_width
                line = f"{empty_line_num} {line}"
            else:
                cur_code_body += line[1:] + "\n"
                line = f"{cur_line_num:>{line_num_width}} {line}"
                cur_line_num += 1
        new_diff += line + "\n"

    if cur_code_body:
        if total_size + len(cur_code_body) <= DIFF_MAX_SIZE:
            codes.append(Code(
                name=f"diff_{len(codes) + 1}",
                body=cur_code_body,
                location=cur_code_loc,
            ))
        else:
            logger.warning("Skipping last code block due to size limit: {} bytes", len(cur_code_body))

    if len(new_diff) > DIFF_MAX_SIZE:
        logger.warning("Diff text size exceeds limit: {} bytes", len(new_diff))
        new_diff = new_diff[:DIFF_MAX_SIZE] + "\n<!-- Diff truncated due to size limit -->"
    return (new_diff, codes)

class ReverserAgent:
    llm: LLM
    builder: StateGraph

    def __init__(self, config: ReverserContext, majority=5, model=DEFAULT_MODEL):
        self.config = config
        if self.config.config_path:
            self.fuzzdb = FuzzDB(self.config.config_path)
            with open(self.config.config_path, "r") as f:
                self.uniafl_conf = json.load(f)
        else:
            self.fuzzdb = None
            self.uniafl_conf = None

        self.crash_logs: Dict[str, str] = {}

        # setup LLM and tools
        self.majority = majority
        # TODO: Disable majority voting for now
        self.majority = 1
        self.code_tools = CodeTool(config, self.uniafl_conf)
        self.model = model
        self.llms: Dict[str, LLM] = {}
        for model in ALL_MODELS:
            model_kwargs = {
                EXTRA_HEADERS: {
                    ANTHROPIC_BETA_HEADER_KEY: ANTHROPIC_BETA_HEADER_VALUE_EXTENDED_CACHE_TTL
                }
            } if model.startswith("claude-") else None
            llm = LLM(model=model, config=config, output_format=ReverserLLMOutput, max_tokens=MAX_TOKENS[model], model_kwargs=model_kwargs)
            self.llms[model] = llm

        self.builder = StateGraph(ReverserOverallState, input=ReverserAgentState, output=ReverserAgentOutputState)
        self.builder.add_node("prepare_prompt", self.prepare_prompt)
        self.builder.add_node("finalize", self.finalize)
        self.builder.add_edge(START, "prepare_prompt")
        # use subgraph to send messages to multiple reverse_one nodes
        self.builder.add_node("reverse_one", self.build_reverse_subgraph())
        # begin_majority is a node that sends the same message to multiple reverse_one nodes
        self.builder.add_edge("prepare_prompt", "reverse_one")
        self.builder.add_edge("reverse_one", "finalize")
        self.builder.add_edge("finalize", END)

    def build_reverse_subgraph(self):
        # Subgraph for reverse with tool and error handling
        builder = StateGraph(ReverseOneState)
        builder.add_node("invoke_llm", self.invoke_llm_with_context)
        builder.add_node("check_testlang", self.check_testlang)
        builder.add_node("update_context", self.update_context)
        builder.add_node("finalize_reverse", self.finalize_reverse)
        builder.add_edge(START, "invoke_llm")
        builder.add_edge("invoke_llm", "check_testlang")
        builder.add_edge("check_testlang", "update_context")
        builder.add_conditional_edges("update_context", self.should_continue,
                                      {True: "invoke_llm", False: "finalize_reverse"})
        builder.add_edge("finalize_reverse", END)
        graph = builder.compile()
        return graph

    def finalize_reverse(self, state: ReverseOneState):
        # Finalize the reverse process
        tl_valid = state["tl_valid"]

        return { "testlang": tl_valid, "messages": state["messages"] }

    def should_continue(self, state: ReverseOneState):
        # Check if we should continue based on the number of trials
        if state["trials"] >= MAX_TRIALS:
            logger.debug("Stop: Max trials reached")
            return False
        # Check if we should continue based on the number of warnings
        if state["warnings"] and state["warning_trials"] >= MAX_WARNING_TRIALS:
            logger.debug("Stop: Max warning trials reached")
            return False
        if state["trials"] >= NO_WARNINGS_TRIALS:
            if not(state["error"] or state["warnings"]):
                if state["llm_output"].codes_to_analyze:
                    # If there are no errors or warnings, and there are still codes to explore
                    return True
                else:
                    # If there are no errors or warnings, and no code to explore
                    logger.debug("Early stop: No more warnings to handle and functions to explore")
                    return False

        # check tl_count
        for tl_hash, count in state["tl_count"].items():
            if state["trials"] > 10 and count >= 5:
                # If the testlang has no updates too many times, stop
                logger.debug(f"Early Stop: the same testlang seen too many times: {tl_hash}")
                return False

        # TODO: check token limit
        return True

    def check_testlang(self, state: ReverseOneState):
        testlang_map = state["testlang_map"].copy()
        new_testlang_id = len(testlang_map)
        tl_valid = state["tl_valid"]
        tl_count = state["tl_count"].copy()
        python_codes = state["python_codes"].copy()
        err_msg = ""
        warnings: List[PyTestLangWarning] = []
        try:
            tl = extract_json(state["llm_output"].sub_testlang)
            if tl_valid:
                tl = testlang.update(tl_valid, tl, state["llm_output"].records_to_remove)
            for python_code in state["llm_output"].python_codes:
                if len(python_code.other_affected_testlang_fields) > 0:
                    raise Exception(f"Python code {python_code.name} should only generate {python_code.target_testlang_field} field in testlang,"
                                    f"but it also generates {len(python_code.other_affected_testlang_fields)} fields: "
                                    f"{[str(ref) for ref in python_code.other_affected_testlang_fields]}."
                                    f"`generator` is dedicated for generating SINGLE FIELD in testlang and don't have access to other fields."
                                    f"Thus, you SHOULD MERGE all testlang fields that `generator` generates into ONE SINGLE FIELD."
                                    f"Otherwise, they will duplicate and overall structure will be wrong.")
                if not python_code.security_vulnerability_severity_score >= 90:
                    warnings.append(PyTestLangWarning(
                        kind="NotSevereSecurityVulnerability",
                        message=f"`{python_code.name}` seems to target not that severe security vulnerabilities."
                                f"Search codes deeper and wider to find really severe security vulnerabilities."
                                f"For C/C++, `AddressSanitizer` can detect `heap-use-after-free`, `heap-buffer-overflow`, `stack-buffer-overflow`, `global-buffer-overflow`, `stack-use-after-return`, `stack-use-after-scope`, `initialization-order-fiasco`, `detected memory leaks`.",
                    ))
                if not python_code.validate_method_score >= 90:
                    warnings.append(PyTestLangWarning(
                        kind="WeakValidateMethod",
                        message=f"`{python_code.name}` seems to have a weak `validate` method. Does it rely on libraries as much as possible to validate rather than manually implementing the validation? "
                                f"Make sure that `{python_code.name}` does not use try-except in `validate` method, so that we can see the exceptions for debugging. "
                                f"Use libraries as much as possible."
                    ))
                constant_ranges = re.findall(r"range\(\d+\)", python_code.code)
                if constant_ranges:
                    warnings.append(PyTestLangWarning(
                        kind="ConstantRangeInPythonCode",
                        message=f"`{python_code.name}` seems to use constant `range` in the code: {constant_ranges[:3]}... "
                                f"This may always generate the same blob and just hinder fuzzing. "
                                f"If these loop counts were precisely calculated or intended to be this value, keep it as is. "
                                f"Otherwise, make it reasonable random number for triggering the security vulnerability. (e.g. from `N` to `max(1, int(random.gauss(N, N * 0.1)))`) "
                                f"However, you SHOULD only use random range in the `generate` and its callee methods, NOT in the `validate` method. "
                                f"Since line coverages don't have how many times the lines were executed, using constant ranges may lead to undetected security vulnerabilities. "
                    ))
            for python_code_name in state["llm_output"].python_codes_to_remove:
                if python_code_name in python_codes:
                    python_codes[python_code_name] = None

            for python_code in state["llm_output"].python_codes:
                workdir = self.config.codegen_dir / "test" / f"{time.time_ns()}"
                os.makedirs(workdir, exist_ok=True)
                python_code_path = workdir / f"{python_code.name}.py"
                with python_code_path.open("w") as f:
                    f.write(python_code.code)
                with tempfile.NamedTemporaryFile(dir=workdir, prefix="input_", suffix=".bin") as input_file:
                    input_data = random.randbytes(1024)
                    input_file.write(input_data)
                    input_file.flush()
                    input_path = Path(input_file.name)
                    output_path = input_path.parent / f"output_of_{input_path.name}"
                    logger.info("Running python code for {} on {}", python_code.name, input_file.name)
                    module_name = f"{python_code.name}.{python_code.name}"

                    for _ in range(10):
                        args = ["python", "-m", "testlang.processing.run",
                                "-i", str(input_path),
                                "-o", str(output_path),
                                "-p", str(workdir),
                                "-t",
                                module_name]
                        proc = subprocess.run(
                            args,
                            stderr=subprocess.PIPE,
                            timeout=10,
                        )
                        if proc.returncode != 0:
                            stderr = proc.stderr.decode("utf-8", errors="replace")
                            err_msg = f"Python code `{python_code.name}` failed to run. Your try-except statements won't work since we hook all exceptions and redirects to you so that you can't ignore potential errors and properly fix them. If this was an error from your own `validate` method, you SHOULD first check if this is a really meaningful validation.\n{stderr}"
                            raise Exception(err_msg)

                    output_data = output_path.read_bytes()
                    logger.info("Output data[:100] for {}: {}", python_code.name, output_data[:100])
                    if len(output_data) > self.config.max_bytes_size:
                        warning = PyTestLangWarning(
                            kind="OutputDataTooLarge",
                            message=(
                                f"Output data for {python_code.name} is too large: {len(output_data)} bytes. "
                                f"Reduce the size of the output data to less than {self.config.max_bytes_size} bytes."
                            )
                        )
                        warnings.append(warning)

                    output_data_to_show = output_data
                    if len(output_data) > 100 * 2:
                        # Show only the first and last 100 bytes
                        output_data_to_show = output_data[:100]
                        output_data_to_show += b"\n(...)\n"
                        output_data_to_show += output_data[-100:]

                    try:
                        output_data_to_show = output_data_to_show.decode("utf-8")
                    except UnicodeDecodeError:
                        output_data_to_show = f"{output_data_to_show}"

                    warning = PyTestLangWarning(
                        kind="GeneratedBlob",
                        message=(f"This is an example of the generated blob by `{python_code.name}`. Does `{python_code.name}` look promising to trigger the bug?\n"
                            f"This isn't about the blob is generated as expected, but about the blob is generated to trigger the security vulnerability.\n"
                            f"If you were to refer to `len`, you should think about if there are any encodings are applied to the blob that would affect the length.\n"
                            f"<python_code_result_example name={python_code.name} len={len(output_data)}>\n"
                            f"{output_data_to_show}\n"
                            f"</python_code_result_example>")
                            )
                    warnings.append(warning)
                python_codes[python_code.name] = python_code

            warnings += testlang.validate(tl, set([name for (name, code) in python_codes.items() if code]))
            for python_code in python_codes.values():
                if python_code and python_code.probability_to_trigger_security_vulnerability_in_percentage >= 90:
                    logger.info("Ignoring warnings for `{}` guaranteed to trigger the bug", python_code.name)
                    warnings = [w for w in warnings if not (list(re.finditer(rf"\b{re.escape(python_code.name)}\b", w.message)) and w.kind not in ["OutputDataTooLarge", "GeneratedBlob"])]

            tl_hash = testlang.hash(testlang.normalize(tl))
            tl_count[tl_hash] = tl_count.get(tl_hash, 0) + 1

            logger.info(f"Waiting for file lock to write testlang and python codes")
            with FileLock(self.config.lock):
                # Write testlang
                timestamp = time.time_ns()

                testlang_path = self.config.outputs / f"{timestamp}.testlang"
                logger.info(f"Writing testlang to {testlang_path}")
                with testlang_path.open("w") as f:
                    f.write(tl)

                # Write python codes
                codegen_dir = self.config.codegen_dir / str(timestamp)
                for python_code in python_codes.values():
                    if python_code is None:
                        continue

                    os.makedirs(codegen_dir, exist_ok=True)
                    codegen_path = codegen_dir / f"{python_code.name}.py"
                    logger.info(f"Writing python code {python_code.name} to {codegen_path}")
                    with codegen_path.open("w") as f:
                        f.write(python_code.code)

                diff_path = self.config.diff_dir / f"{new_testlang_id}.txt"
                with diff_path.open("w") as f:
                    logger.info(f"Writing diff to {diff_path}")
                    f.write(state["llm_output"].diff)

                if self.config.visualize_output:
                    png_dir = self.config.workdir / "visualized"
                    os.makedirs(png_dir, exist_ok=True)
                    output_path = png_dir / f"testlang_{new_testlang_id}.png"

                    live_python_codes = {
                        name: code.code
                        for name, code in python_codes.items()
                        if code
                    }
                    testlang.visualize(tl, live_python_codes, str(output_path))

            # testlang should be correct if it passed both validation and hashing
            tl_valid = tl
            testlang_map[new_testlang_id] = tl_valid
        except Exception as e:
            logger.debug("Error in testlang: {}", e)
            err_msg = str(e)
            python_codes = state["python_codes"].copy()

        # FIXME: DO NOT str all warnings
        # TODO: Prioritize warnings based on severity
        # Ignored warnings will appear in the next step unless the problem is resolved
        return { "tl_valid": tl_valid, "python_codes": python_codes, "warnings": [str(w) for w in warnings[:10]], "error": err_msg, "tl_count": tl_count, "testlang_map": testlang_map }

    async def update_context(self, state: ReverseOneState):
        llm_output = state["llm_output"]
        trials = state["trials"]
        warnings = state["warnings"]
        warning_trials = state["warning_trials"]
        suppressed_warnings = state["suppressed_warnings"]
        code_blocks = state["code_blocks"]
        error = state["error"]

        if error:
            # If there is an error, we should keep the previous context
            return { "llm_output": llm_output, "warnings": [], "error": error }

        new_code_blocks = await self.get_code_blocks(code_blocks, llm_output.codes_to_analyze)
        error_code_blocks = [code for code in new_code_blocks if code.error]
        if error_code_blocks:
            code_error_msg = f"<code_search_errors>\n"
            for idx, code in enumerate(error_code_blocks):
                code_error_msg += f"<code_search_error_{idx}>\n"
                code_error_msg += f"{str(code)}\n"
                code_error_msg += f"</code_search_error_{idx}>\n\n"
            code_error_msg += f"</code_search_errors>\n"
            warnings.append(code_error_msg)
            new_code_blocks = [code for code in new_code_blocks if not code.error]

        if warnings:
            # Filter out suppressed warnings
            warnings = [w for w in warnings if w not in suppressed_warnings]

            # TODO: context can be too large if warnings are too many
            if warnings:
                # If there are remaining warnings, we should keep the previous context
                # along with the new context
                next_code_blocks = list(set(code_blocks + new_code_blocks))
                return { "llm_output": llm_output, "warnings": warnings, "error": "",
                         "code_blocks": next_code_blocks }

        next_code_blocks = new_code_blocks
        for idx, code in enumerate(code_blocks):
            if idx in llm_output.suppressed_codes:
                # If the code block is skipped, we should not add it again
                continue
            elif code not in next_code_blocks:
                next_code_blocks.append(code)

        # TODO: save some context for next round
        return { "llm_output": llm_output, "warnings": [], "error": "",
                 "code_blocks": next_code_blocks }

    async def get_code_blocks(self, code_blocks: List[Code], references: List[CodeReference]) -> List[Code]:
        """
        Given a list of CodeReference entries, asynchronously retrieve corresponding code blocks.
        If multiple candidates are returned, each will be annotated with its index for disambiguation.
        """
        given_codes = [self.harness_code] + self.diff_codes + code_blocks

        async def resolve(ref: CodeReference) -> List[Code]:
            if '.' in ref.name:
                i = ref.name.rfind('.')
                if ref.class_name is None:
                    ref.class_name = ref.name[:i]
                ref.name = ref.name[i+1:]
            try:
                if ref.file_path and ref.line_num:
                    if not any([(code.location and
                                code.location.file_path == ref.file_path and
                                code.location.start_line <= ref.line_num <= code.location.end_line)
                                for code in given_codes]):
                        raise LanuageServerArgumentError(f"DO NOT guess the code location: ({ref}). Find it from the given `code_blocks` or the harness path in the <harness>.")

                codes = await self.code_tools.search_code(
                    name=ref.name,
                    file_path=ref.file_path,
                    line_num=ref.line_num,
                    class_name=ref.class_name,
                    candidates_index=None
                )
                if not codes:
                    logger.debug(f"Code not found for reference: {ref.name}")
                    return []
            except LanuageServerArgumentError as e:
                ref_codes: List[Code] = []
                for code in given_codes:
                    if code.error or not code.body:
                        continue

                    for (i, line) in enumerate(code.body.splitlines()):
                        if ref.name == "h" and line.startswith("#include "):
                            logger.warning(f"Ignoring line suggestion for `{ref.class_name}.{ref.name}`:\n```{line}``` ")
                            continue

                        if list(re.finditer(rf"\b{re.escape(ref.name)}\b", line)):
                            ref_code_loc = None
                            if code.location:
                                line_num = code.location.start_line + i
                                ref_code_loc = CodeLocation(
                                    file_path=code.location.file_path,
                                    start_line=line_num,
                                    end_line=line_num,
                                )
                            ref_code = Code(
                                name=f"{ref.name}_ref",
                                body=line,
                                location=ref_code_loc
                            )
                            ref_codes.append(ref_code)

                err_msg = str(e)
                if not ref_codes:
                    err_msg += f"\nNo code is using `{ref.name}` in the given codes. In this case only, you can search without `line_num`, but be mindful that you could get multiple or inaccurate results."
                else:
                    err_msg += "\nSearch again from following code references:\n"
                    err_msg += f"<code_refs name={ref.name} length={len(ref_codes)}>\n"
                    for i, code in enumerate(ref_codes):
                        loc_attrs = f' file_path="{code.location.file_path}" line_num="{code.location.start_line}"' if code.location else ""
                        err_msg += f"<code_ref_{i} name={ref.name} {loc_attrs}>\n"
                        err_msg += f"{code.body}\n"
                        err_msg += f"</code_ref_{i}>\n"
                    err_msg += "</code_refs>\n"

                logger.error("Error in search_code - {}", e)
                return [Code(error=err_msg)]
            except Exception as e:
                logger.debug("Error in search_code - {}", e, exc_info=True)
                return []

            return codes

        result_groups = await asyncio.gather(*(resolve(ref) for ref in references))
        return [block for group in result_groups for block in group]

    async def build_context_msgs(self, state: ReverseOneState) -> List[BaseMessage]:
        # Build context message for reverse_one
        testlang_map = state["testlang_map"].copy()
        tl_valid = state["tl_valid"]
        error = state["error"]
        warnings = state["warnings"]

        special_warnings = []

        if not tl_valid:
            # First time to generate testlang
            context_msg = "<current-testlang> </current-testlang>\n"
            context_msg += ("<note>\nNo testlang yet.\n"
                            'Try to start analysis by making a default testlang with `INPUT` record and appropriate "mode" and "default_endian"\n'
                            'This SHOULD have "is_partial": false \n'
                            "Keep testlang and generator simple until you find security vulnerabilities:\n"
                            "</note>\n")
        else:
            cur_testlang_id = len(testlang_map) - 1
            context_msg = f"<current_testlang_{cur_testlang_id}>\n{tl_valid}\n</current_testlang_{cur_testlang_id}>\n\n"

        python_codes = state["python_codes"]
        if python_codes:
            python_code_msg = f"<python_codes>\n"
            for name, python_code in python_codes.items():
                if python_code is None:
                    continue

                python_code_msg += f"<python_code name={name}>\n"
                python_code_msg += f"{str(python_code.code)}\n"
                python_code_msg += f"</python_code>\n\n"
            python_code_msg += f"</python_codes>\n\n"
            context_msg += python_code_msg

        crash_msg = ""
        cov_msg = ""
        if self.fuzzdb:
            # `corpus_map` doesn't have povs and povs are from all other `*_input_gen`s too.
            if self.config.pov_dir:
                if len(self.crash_logs) < 3:
                    seeds = self.fuzzdb.list_seeds_new()
                    all_povs = set([seed.name for seed in seeds if seed.directory == self.config.pov_dir])
                    new_povs = all_povs.difference(self.crash_logs.keys())
                    # Prevent overwhelming povs due to failure to deduplication (e.g. UAF)
                    # TODO: Sample only one UAF pov
                    while len(self.crash_logs) < 3 and new_povs:
                        pov_name = new_povs.pop()
                        # Try hidden file first, fallback to non-hidden for old POVs
                        crash_log_path = self.config.pov_dir / f".{pov_name}.crash_log"
                        if not crash_log_path.exists():
                            crash_log_path = self.config.pov_dir / f"{pov_name}.crash_log"
                        if not crash_log_path.exists():
                            continue
                        with open(crash_log_path, "r", errors="replace") as f:
                            crash_log = f.read()

                            # Trim
                            m = re.search(r"== Java Exception: .*", crash_log, re.DOTALL)
                            if m:
                                crash_log = m.group(0)
                                # Parse `main` thread on `Stack traces of all JVM threads:`
                                STACK_TRACES_MSG = "Stack traces of all JVM threads:\n"
                                stack_traces_idx = crash_log.find(STACK_TRACES_MSG)
                                if stack_traces_idx != -1:
                                    stack_traces = crash_log[stack_traces_idx:]
                                    crash_log = crash_log[:stack_traces_idx + len(STACK_TRACES_MSG)]
                                    thread_main_idx = stack_traces.find("Thread[main,")
                                    if thread_main_idx != -1:
                                        stack_trace = stack_traces[thread_main_idx:]
                                        end = stack_trace[1:].find("Thread[")
                                        if end != -1:
                                            stack_trace = stack_trace[:end + 1]
                                        crash_log += stack_trace
                            m = re.search(r"(==\d+==ERROR: .*)SUMMARY: ", crash_log, re.DOTALL)
                            if m:
                                crash_log = m.group(1)
                            self.crash_logs[pov_name] = crash_log

                if self.crash_logs:
                    # Sort for cache
                    pov_names = sorted(self.crash_logs.keys())
                    crash_msg += f"<crash_logs>\n\n"
                    for pov_name in pov_names:
                        crash_msg += f"<crash_log_{pov_name}>\n\n"
                        crash_msg += f"{self.crash_logs[pov_name]}\n"
                        crash_msg += f"</crash_log_{pov_name}>\n\n"
                    crash_msg += f"</crash_logs>\n\n"
                    special_warnings.append(f"You SHOULD NOT target already found security vulnerabilities (i.e. {pov_names}) "
                                             "and instead try to find new security vulnerabilities "
                                             "by exploring not analyzed codes according to `analysis` in testlang. "
                                             "Also, remove records or python codes dedicated for these vulnerabilities from testlang. "
                                             "However, you SHOULD NOT remove parts that are general and still relevant to the future analysis. ")

            # All corpus in `corpus_map` is from `testlang_input_gen`.
            if self.config.corpus_map:
                latest_testlang_id = max(testlang_map.keys(), default=None)
                logger.info("Latest testlang ID: {}", latest_testlang_id)

                # Wait for the latest testlang to generate, execute and hopefully update coverages
                async def wait_for_used_testlangs() -> List[int]:
                    if not (self.config.used_testlangs and latest_testlang_id is not None):
                        return []

                    start = time.time()
                    while True:
                        logger.info("Waiting for file lock to read used testlangs")
                        with FileLock(self.config.lock):
                            with self.config.used_testlangs.open("r") as f:
                                logger.info("Reading used testlangs from {}", self.config.used_testlangs)
                                used_testlang_ids: List[int] = json.load(f)
                                logger.info("Used testlangs: {}", used_testlang_ids)
                                if self.config.used_testlangs_timeout <= 0:
                                    logger.info(f"Used testlangs timeout is disabled, returning immediately: {used_testlang_ids}")
                                    return used_testlang_ids

                                if latest_testlang_id in used_testlang_ids:
                                    logger.info("Latest testlang ID {} is in used testlangs", latest_testlang_id)
                                    end = time.time()
                                    logger.info("Used testlangs updated in {:.2f} seconds", end - start)
                                    return used_testlang_ids

                                logger.info("Waiting for used testlangs to be updated")
                                await asyncio.sleep(3)

                used_testlang_ids: List[int] = []
                if self.config.used_testlangs and latest_testlang_id is not None:
                    try:
                        logger.info(f"Waiting for used testlangs to be updated (timeout: {self.config.used_testlangs_timeout}s)")
                        timeout = None if self.config.used_testlangs_timeout <= 0 else self.config.used_testlangs_timeout
                        used_testlang_ids = await asyncio.wait_for(wait_for_used_testlangs(), timeout=timeout)
                    except asyncio.TimeoutError:
                        err_msg = traceback.format_exc()
                        logger.error("Error waiting for used testlangs: {}", err_msg)

                # We only provide coverage information after the latest testlang is used
                if not self.config.used_testlangs or (latest_testlang_id is not None and latest_testlang_id in used_testlang_ids):
                    corpus_map: Dict[str, int] = {}
                    with FileLock(self.config.lock):
                        with self.config.corpus_map.open("r") as f:
                            corpus_map = json.load(f)

                        def update_node_cov(old_node_cov: Dict[str, CovInfo], new_node_cov: Dict[str, CovInfo]):
                            for func_name, cov_info in new_node_cov.items():
                                if func_name not in old_node_cov:
                                    old_node_cov[func_name] = cov_info
                                else:
                                    old_cov_info = old_node_cov[func_name]
                                    if cov_info.src != old_cov_info.src:
                                        logger.error("Node coverage for {} has different src: {} vs {}", func_name, cov_info.src, old_cov_info.src)
                                        return

                                    new_cov_info = CovInfo(
                                        func_name=func_name,
                                        src=old_cov_info.src,
                                        lines=sorted(set(old_cov_info.lines).union(cov_info.lines)),
                                    )
                                    old_node_cov[func_name] = new_cov_info

                        def diff_node_cov(node_cov_1: Dict[str, CovInfo], node_cov_2: Dict[str, CovInfo]) -> Dict[str, CovInfo]:
                            diff = {}
                            for func_name, cov_info in node_cov_1.items():
                                if func_name not in node_cov_2:
                                    diff[func_name] = cov_info
                                else:
                                    new_lines = sorted(set(cov_info.lines).difference(node_cov_2[func_name].lines))
                                    if new_lines:
                                        diff[func_name] = CovInfo(
                                            func_name=func_name,
                                            src=cov_info.src,
                                            lines=new_lines,
                                        )
                            return diff

                        def format_node_cov(node_cov: Dict[str, CovInfo]) -> List[str]:
                            return [str(cov_info) for cov_info in node_cov.values()]

                        # {testlang_id: {func_name: CovInfo}}
                        node_cov_map: Dict[int, Dict[str, CovInfo]] = defaultdict(dict)
                        if isinstance(corpus_map, dict):
                            for seed_name in corpus_map:
                                testlang_id = corpus_map[seed_name]
                                old_node_cov = node_cov_map[testlang_id]
                                try:
                                    node_cov = self.fuzzdb.load_node_cov(seed_name)
                                    update_node_cov(old_node_cov, node_cov)
                                except:
                                    err_msg = traceback.format_exc()
                                    logger.error("Error loading node coverage for {}:\n{}", seed_name, err_msg)

                        if node_cov_map:
                            target_node_cov: Dict[str, CovInfo] = {}
                            if "llm_output" in state:
                                for lines in state["llm_output"].lines_to_cover:
                                    cov_info = lines.to_cov_info()
                                    target_node_cov[cov_info.func_name] = cov_info
                            funcs_to_cover = set(target_node_cov.keys())

                            all_node_cov: Dict[str, CovInfo] = {}
                            for testlang_id, node_cov in sorted(node_cov_map.items()):
                                updated_node_cov = diff_node_cov(node_cov, all_node_cov)
                                if updated_node_cov:
                                    logger.info("Updated node coverage for testlang {}: {}", testlang_id, format_node_cov(updated_node_cov))
                                    diff_path = self.config.diff_dir / f"{testlang_id}.txt"
                                    with diff_path.open("r", errors="replace") as f_diff:
                                        tmp_cov_msg = ""
                                        for func_name, cov_info in updated_node_cov.items():
                                            if func_name not in funcs_to_cover:
                                                continue

                                            tmp_cov_msg += str(cov_info) + "\n"
                                        if tmp_cov_msg:
                                            cov_msg += f"<new_line_coverages_by_testlang_{testlang_id} diff=\"{f_diff.read()}\">\n"
                                            cov_msg += tmp_cov_msg
                                            cov_msg += f"</new_line_coverages_by_testlang_{testlang_id}>\n\n"
                                update_node_cov(all_node_cov, node_cov)

                            if target_node_cov:
                                uncovered_node_cov = diff_node_cov(target_node_cov, all_node_cov)
                                if uncovered_node_cov:
                                    prefix = "Some"
                                    if uncovered_node_cov == target_node_cov:
                                        prefix = "ALL"
                                        if self.config.deprioritized_testlangs:
                                            with self.config.deprioritized_testlangs.open("w") as f:
                                                logger.info("Deprioritizing testlangs: {}", used_testlang_ids)
                                                json.dump(used_testlang_ids, f, indent=2)

                                    special_warnings.append(f"{prefix} of your `lines_to_cover`: {format_node_cov(uncovered_node_cov)} have never been covered by any corpus yet.\n"
                                                            "If you've already got crash_log that should've covered those lines, you can ignore this warning and pivot to target other security vulnerabilities since line coverages will not be updated by the crash_log.\n"
                                                            "If not, are these lines essential to trigger the security vulnerabilities? (You won't cover empty or comment lines.)\n"
                                                            "If so, THIS IS A PROBLEM!\n"
                                                            "Check `analysis` in testlang and reason about why your testlang is not generating blobs that can cover those lines.\n"
                                                            "If `analysis` is not sufficient, you SHOULD add enough `analysis` in testlang by searching more codes.\n"
                                                            "You SHOULD fix the testlang or python codes to cover those lines "
                                                            "if they are important for the security vulnerabilities.\n")
                                else:
                                    special_warnings.append("All `lines_to_cover` are covered by the corpus. Good job! "
                                                            "Are there more essential lines to cover to trigger the security vulnerabilities? "
                                                            "If not, have you actually found security vulnerabilities and can you see it in the `crash_logs`? "
                                                            "If not, check if any loop needs to run certain times since coverages cannot know how many times the line should be executed. "
                                                            "If so, you can try to calculate reasonable number or random range for the loop count to trigger the security vulnerabilities.\n")

        code_blocks_msg = self.make_code_blocks_msg(state["code_blocks"])

        context_msgs = []
        if crash_msg:
            crash_human_msg = HumanMessage(inspect.cleandoc(crash_msg), name="already_found_security_vulnerabilties")
            self.cache_anthropic_msg(crash_human_msg)
            context_msgs.append(crash_human_msg)
        if code_blocks_msg:
            context_msgs.append(HumanMessage(inspect.cleandoc(code_blocks_msg), name="code_blocks"))
        if cov_msg:
            context_msgs.append(HumanMessage(inspect.cleandoc(cov_msg), name="coverage_by_generated_blobs"))
        if context_msg:
            context_msgs.append(HumanMessage(inspect.cleandoc(context_msg), name="current_context"))

        warnings += special_warnings

        if error:
            llm_output = state["llm_output"]
            python_code_msg = f"<python_codes>\n"
            for python_code in llm_output.python_codes:
                python_code_msg += f"<python_code name={python_code.name}>\n"
                python_code_msg += f"{str(python_code.code)}\n"
                python_code_msg += f"</python_code>\n\n"
            python_code_msg += f"</python_codes>\n\n"
            last_response = AIMessage(content=f""
                                      f"<chain_of_thought>\n{llm_output.chain_of_thought}\n</chain_of_thought>\n"
                                      f"<sub_testlang>\n{llm_output.sub_testlang}\n</sub_testlang>\n"
                                      f"<records_to_remove>\n{list(llm_output.records_to_remove)}\n</records_to_remove>\n"
                                      f"{python_code_msg}")
            fmted_error = f"\n<error>\n{error}\n</error>"
            error_msg = inspect.cleandoc(f"Your previous answer was not valid. Please try again.\n"
                                         f"The error was: {fmted_error}\n"
                                         f"Please change your response in accordance with the error.\n"
                                         f"You SHOULD fix the error first, and then enhance the testlang.\n" )
            return context_msgs + [last_response, HumanMessage(inspect.cleandoc(error_msg), name="error")]

        elif warnings:
            # tl_valid is already updated with the sub testlang
            fmted_warnings = [f"<warning_{i}>\n{w}\n(If you think this warning is resolved, ignore this.)\n</warning_{i}>" for i, w in enumerate(warnings)]
            fmted_warnings = f"\n<warnings>\n\n{(chr(10) * 2).join(fmted_warnings)}\n\n</warnings>"

            warning_msg = inspect.cleandoc("""Current testlang is valid, but there were warnings:
            {}

            Please enhance testlang in accordance with the warnings.
            Try to make the testlang more detailed and complete, using the information from the warnings and chain-of-thought process.
            If you think some warnings are wrong and want to suppress them, please give the list of numbers of the warnings you want to suppress.
            (e.g. "suppressed_warnings": [0, 1, 2])
            """).format(fmted_warnings)

            return context_msgs + [HumanMessage(inspect.cleandoc(warning_msg), name="warnings")]

        return context_msgs

    def make_code_blocks_msg(self, code_blocks: List[Code]) -> str:
        code_blocks_msg = ""
        if code_blocks:
            code_blocks_msg += f"<code-blocks>\n"
            for idx, code in enumerate([code for code in code_blocks if not code.error]):
                code_blocks_msg += f"<code-block idx={idx}>\n"
                code_blocks_msg += f"{str(code)}\n"
                code_blocks_msg += f"</code-block>\n\n"
            code_blocks_msg += f"</code-blocks>\n\n"

            if len(code_blocks) > 10 or len(code_blocks_msg) > 4000:
                code_blocks_msg += "<note>\nThere are too many code blocks. Try to use `suppressed_codes`\n</note>\n"
        return code_blocks_msg

    def choose_llm(self, state: ReverseOneState, last_llm: LLM | None) -> LLM:
        if "error" in state and "Traceback (most recent call last):" in state["error"]:
            if not (last_llm and last_llm.model_name == CODING_MODEL):
                return self.llms[CODING_MODEL]

        models = set(ALL_MODELS)
        if len(models) > 1 and last_llm:
            models.discard(last_llm.model_name)
        return self.llms[random.choice(list(models))]

    async def invoke_llm_with_context(self, state: ReverseOneState):
        messages = state['messages'].copy()
        llm = None
        cache_control_contents = []
        rate_limit_count = 0
        for retry_count in range(20):
            try:
                context_msgs = await self.build_context_msgs(state)
                messages = state['messages'] + context_msgs
                llm = self.choose_llm(state, llm)
                non_base_msgs = [m for m in messages if not (m.name and m.name.startswith("base_msg_"))]
                logger.info("[LLM requests][{}]\n{}", llm.model_name, non_base_msgs)
                try:
                    responses = await llm.ainvoke(messages)
                    llm_output: ReverserLLMOutput = responses[-1]
                except anthropic.BadRequestError as e:
                    e_str = str(e)
                    logger.error("anthropic.BadRequestError: {}", e_str)

                    logger.info("Retrying after 5 seconds")
                    await asyncio.sleep(5)

                    if ANTHROPIC_BETA_HEADER_KEY in e_str:
                        if not isinstance(llm.chat_model, ChatAnthropic):
                            logger.error("LLM chat model is not an instance of anthropic.ChatAnthropic")
                            raise

                        logger.info(f"Removing {ANTHROPIC_BETA_HEADER_KEY} from model kwargs for {llm.model_name} and retrying")
                        if not hasattr(llm.chat_model, "model_kwargs"):
                            logger.error("LLM chat model does not have model_kwargs attribute")
                            raise

                        llm.chat_model.model_kwargs.get(EXTRA_HEADERS, {}).pop(ANTHROPIC_BETA_HEADER_KEY, None)
                        try:
                            responses = await llm.ainvoke(messages)
                            llm_output: ReverserLLMOutput = responses[-1]
                        except anthropic.BadRequestError as e:
                            logger.error("Anthropic BadRequestError after removing {}: {}", ANTHROPIC_BETA_HEADER_KEY, str(e))
                            if "ttl: Extra inputs are not permitted" not in str(e):
                                raise

                            logger.info("Retrying with default cache_control contents after 5 seconds")
                            await asyncio.sleep(5)

                            for msg in messages:
                                if isinstance(msg, BaseMessage) and isinstance(msg.content, list):
                                    for content in msg.content:
                                        if isinstance(content, dict) and "cache_control" in content:
                                            cache_control_contents.append(content)
                                            logger.info(f"Content: {content}")
                                            content["cache_control"] = {"type": "ephemeral"}
                                            logger.info(f"Content: {content}")

                            responses = await llm.ainvoke(messages)
                            llm_output: ReverserLLMOutput = responses[-1]
                        finally:
                            llm.chat_model.model_kwargs.get(EXTRA_HEADERS, {})[ANTHROPIC_BETA_HEADER_KEY] = ANTHROPIC_BETA_HEADER_VALUE_EXTENDED_CACHE_TTL
                            for content in cache_control_contents:
                                content["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                    elif ("No models have context window large enough for this call" in e_str or
                          "prompt is too long: " in e_str):
                        try:
                            self.reduce_input_tokens(messages, state["code_blocks"])
                            responses = await llm.ainvoke(messages)
                            llm_output: ReverserLLMOutput = responses[-1]
                        except:
                            raise e
                    elif m := re.search(r"input length and `max_tokens` exceed context limit: (\d+) \+ (\d+) > (\d+),", e_str):
                        try:
                            input_tokens = int(m.group(1))
                            all_tokens = int(m.group(3))

                            half_max_tokens = MAX_TOKENS[llm.model_name] / 2
                            new_max_tokens = all_tokens - input_tokens - 1000
                            if new_max_tokens < half_max_tokens:
                                new_max_tokens = half_max_tokens
                                self.reduce_input_tokens(messages, state["code_blocks"])

                            responses = await llm.ainvoke(messages, max_tokens=new_max_tokens)
                            llm_output: ReverserLLMOutput = responses[-1]
                        except:
                            raise e
                    elif m := re.search(r"exceed the rate limit for your organization \([^\)]+\) of [0-9,]+ output tokens", e_str):
                        try:
                            half_max_tokens = MAX_TOKENS[llm.model_name] / 2
                            responses = await llm.ainvoke(messages, max_tokens=half_max_tokens)
                            llm_output: ReverserLLMOutput = responses[-1]
                        except:
                            raise e
                    else:
                        raise
                logger.info("[LLM responses][{}]\n{}", llm.model_name, llm_output)
                if state["warnings"]:
                    # LLM may have suppressed some warnings
                    # check it here as new warnings may be added in check_testlang
                    suppressed_warnings = set()
                    for idx in llm_output.suppressed_warnings:
                        if idx < len(state["warnings"]):
                            suppressed_warnings.add(state["warnings"][idx])
                    return { "llm_output": llm_output, "trials": state["trials"] + 1,
                             "warning_trials": state["warning_trials"] + 1,
                             "suppressed_warnings": suppressed_warnings }
                return { "llm_output": llm_output, "trials": state["trials"] + 1 }
            except Exception as e:
                err_msg = traceback.format_exc()
                logger.error("Error in LLM:\n{}", err_msg)
                sleep_time = 5 * retry_count
                if isinstance(e, anthropic.RateLimitError):
                    sleep_time = min(4 * (2 ** rate_limit_count), 65)
                    rate_limit_count += 1
                await asyncio.sleep(sleep_time)
        # If we reach here, it means we failed to get a response from the LLM
        # TODO: should we return or raise an error?
        return { "trials": state["trials"] + 1, "code_blocks": [code for code in state["code_blocks"] if not code.error] }

    def reduce_input_tokens(self, messages: List[BaseMessage], codes: List[Code]):
        REDACT ="// <SUPPRESS UNNECESSARY CODES OR WARNINGS TO SEE THIS.>"

        def redact_code(code: Code) -> Code:
            return Code(
                name=code.name,
                body=REDACT,
                location=code.location,
            )

        for msg in messages:
            if not isinstance(msg, BaseMessage):
                continue

            if msg.name == "code_blocks" and isinstance(msg.content, str):
                new_codes = [redact_code(code) for code in codes if not code.error and code.body]
                msg.content = self.make_code_blocks_msg(new_codes)
            elif msg.name == "warnings" and isinstance(msg.content, str):
                msg.content += f"\n\n<critical_warning>Resolve `{REDACT}` by checking `name` and `file_path`.</critical_warning>\n"

    def cache_anthropic_msg(self, msg: BaseMessage):
        if isinstance(msg, BaseMessage) and isinstance(msg.content, str):
            msg.content = [
                {
                    "text": msg.content,
                    "type": "text",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]

    def prepare_prompt(self, state):
        # TODO: support other langs
        harness_code = load_file(state["harness_path"])
        # replace switch-case values with constants from IR
        # harness_code = switch_case_constant_patch(harness_code)
        # clang -E pass
        # harness_code = preprocessor_pass('harness', harness_code)
        self.harness_code = Code(
            name="",
            body=harness_code,
            location=CodeLocation(
                file_path=str(state["harness_path"]),
                start_line=1,
                end_line=len(harness_code.splitlines()) + 1,
            ),
        )
        self.harness_code = self.code_tools.filter_code(self.harness_code)
        try:
            diff, self.diff_codes = process_git_diff(load_file(state["diff_path"]) if state["diff_path"] else "")
        except Exception as e:
            logger.error("Error processing git diff: {}", e)
            diff = None
            self.diff_codes = []

        system_msg = load_file(PROMPT_DIR / 'SYSTEM')
        TAGS = f"<{JSON_SCHEMA_TAG_NAME}></{JSON_SCHEMA_TAG_NAME}>"
        CUSTOM_ID_TAGS = f"<{CUSTOM_TYPE_IDS_TAG_NAME}></{CUSTOM_TYPE_IDS_TAG_NAME}>"
        grammar = load_file(PROMPT_DIR / 'GRAMMAR')
        schema = testlang.schema()
        grammar = grammar.replace(TAGS, TAGS.replace("></", f">\n{schema}\n</"))
        grammar = grammar.replace(CUSTOM_ID_TAGS, CUSTOM_ID_TAGS.replace("></", f">\n{fetch_generators()}\n</"))
        examples = load_file(PROMPT_DIR / 'EXAMPLES')
        examples += load_file(PROMPT_DIR / 'EXAMPLES_EXT')
        examples += load_file(PROMPT_DIR / 'EXAMPLES_CUSTOM')

        target = f"<harness>\n{self.harness_code}\n</harness>\n"
        if diff:
            target += f"<diff>\n{diff}\n</diff>\n"

        messages = [SystemMessage(system_msg, name="base_msg_role"),
                    SystemMessage(grammar, name="base_msg_grammar"),
                    HumanMessage(examples, name="base_msg_examples"),
                    HumanMessage(target, name="base_msg_target")]

        if 'claude' in self.model:
            for msg in messages:
                if isinstance(msg, BaseMessage) and msg.name and (msg.name.endswith("_examples") or msg.name.endswith("_target")):
                    self.cache_anthropic_msg(msg)

        testlang_map = {}
        for testlang_path in self.config.workdir.glob("testlang_*.out"):
            m = re.match(r"testlang_(\d+).out", testlang_path.name)
            if m:
                try:
                    testlang_id = int(m.group(1))
                    testlang_map[testlang_id] = testlang_path.read_text()
                except:
                    logger.error(f"Failed to read testlang from {testlang_path}: {traceback.format_exc()}")

        # TODO: Reflect at rerun
        return {
            "messages": messages,
            "trials": 0,
            "warning_trials": 0,
            "warnings": [],
            "suppressed_warnings": set(),
            "error": "",
            "tl_valid": "",
            "testlang_map": testlang_map,
        }

    def finalize(self, state):
        return {}

    def compile(self):
        self.graph = self.builder.compile()
        return self.graph
