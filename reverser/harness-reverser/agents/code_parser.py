import asyncio
import re
from pathlib import Path
import dataclasses
from typing import Optional, List, Dict, Set
from collections import defaultdict
import json
import logging

from langchain.tools import BaseTool, tool
from loguru import logger
import multilspy.language_server
import multilspy.multilspy_logger
from multilspy.multilspy_config import MultilspyConfig
import multilspy.multilspy_logger
from tools.context import ReverserContext
import mlla.codeindexer.codeindexer
from mlla.utils.context import get_common_paths
from mlla.utils.llm_tools.astgrep import AGTool, RetrievalResult
import subprocess
import functools
import os

@dataclasses.dataclass(frozen=True)
class CodeLocation:
    file_path: str
    start_line: int
    end_line: int

@dataclasses.dataclass(frozen=True)
class Code:
    name: Optional[str] = None
    body: Optional[str] = None
    location: Optional[CodeLocation] = None
    # FIXME: Remove this when the error handling is done
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.error:
            return self.error

        def format_line(line_num, line):
            line_num = str(line_num).rjust(len(str(self.location.end_line)))
            return f"{line_num} {line}"
        lines = self.body.splitlines()
        lines = [format_line(line_num, line) for line_num, line in zip(range(self.location.start_line, self.location.end_line + 1), lines)]
        return f"// name: {self.name}\n// file_path: {self.location.file_path}\n{chr(10).join(lines)}"

class MyAGTool(AGTool):
    def __init__(self):
        super().__init__()
        self.decl_cache = {}

    def search_declaration(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        """
        Public method to search for variable declarations.
        """
        lang = self._language_from_file_path(file_path)
        if lang == "c" or lang == "cpp":
            return self._search_declaration_C(regex, file_path)
        else:
            logger.warning(f"Variable declaration search not implemented for language: {lang}")
            return []

    def _search_declaration_C(
        self, regex: str, file_path: str
    ) -> list[RetrievalResult]:
        """
        Search for variable declarations (global, static, or local) in a C file.
        :param regex: The regular expression to match the variable name.
        :param file_path: The path to the file.
        :return: A list of RetrievalResult objects.
        """
        retrieval_results: list[RetrievalResult] = []
        lang = self._language_from_file_path(file_path)
        if not lang:
            return []
        root_node, file_src = self._get_root_node(file_path, lang)
        if not root_node:
            logger.warning(f"ASTGrep: Could not get root node for {file_path}")
            return []

        if file_path not in self.decl_cache:
            logger.info(f"ASTGrep: Searching all declarations in {file_path}")

            # The filtering for the specific identifier matching 'regex' will happen inside the loop.
            self.decl_cache[file_path] = root_node.find_all(kind="declaration")
            logger.info(f"ASTGrep: Found {len(self.decl_cache[file_path])} total 'declaration' nodes in {file_path} to process.")

        declaration_nodes = self.decl_cache[file_path]

        if not declaration_nodes:
            logger.warning(f"ASTGrep: No 'declaration' nodes of any kind found in {file_path}. Cannot search for variable '{regex}'.")
            return []

        for node in declaration_nodes:
            for id_node in node.find_all(kind="identifier", regex=regex):
                retrieval_results.append(
                    RetrievalResult(
                        lang=lang,
                        file_path=file_path,
                        **self._retrieve_code_from_node(node, file_src, id_node.text()),
                    )
                )
        return retrieval_results

class MultilspyLogger(multilspy.multilspy_logger.MultilspyLogger):
    def log(self, debug_message: str, level: int, sanitized_error_message: str = "") -> None:
        msg = debug_message.replace("'", '"').replace("\n", " ")
        log_level = logging.getLevelName(level).lower()
        if hasattr(logger, log_level):
            log = getattr(logger, log_level)
            log(msg)
        else:
            logger.warning(f"Unknown log level '{log_level}' provided to MultilspyLogger, defaulting to INFO for message")
            logger.info(msg)

class LanuageServerArgumentError(Exception):
    def __init__(self, message: str):
        message = f"{message}\nCheck `name` and `file_path` and `line_num` and search AGAIN."
        super().__init__(message)

class LanguageServer:
    def __init__(self, config: ReverserContext):
        self.config = config
        self.repo_root_path = config.cp.cp_src_path.resolve().as_posix()
        self.ag_tool = MyAGTool()
        # Attributes for the single, long-lived LSP client
        self.lsp_client: Optional[multilspy.language_server.LanguageServer] = None
        self.lsp_logger = MultilspyLogger()
        self._lsp_init_lock = asyncio.Lock()
        self._lsp_server_active = False
        logger.info(f"[LanguageServer] Instance created for {self.repo_root_path}. LSP client not yet initialized.")

    async def init(self):
        """
        Initializes the LSP client and starts its server context if not already done.
        This method is designed to be called concurrently and will ensure initialization
        happens only once.
        """
        if self.lsp_client and self._lsp_server_active:
            return # Already initialized and active

        async with self._lsp_init_lock:
            # Double-check after acquiring the lock
            if self.lsp_client and self._lsp_server_active:
                return

            if not self.lsp_client:
                logger.info("[LanguageServer] Initializing LSP client (Client Mode)...")
                if self.config.cp.language == "c" or self.config.cp.language == "c++":
                    code_language = "c"
                elif self.config.cp.language == "jvm":
                    code_language = "java"
                else:
                    code_language = self.config.cp.language

                lsp_config_dict = {"code_language": code_language, "is_server": False}
                lsp_config = MultilspyConfig.from_dict(lsp_config_dict)
                self.lsp_client = multilspy.language_server.LanguageServer.create(lsp_config, self.lsp_logger, str(self.repo_root_path))
                logger.info("[LanguageServer] LSP client created.")

            if self.lsp_client and not self._lsp_server_active:
                logger.info("[LanguageServer] Starting LSP server context...")
                try:
                    await self.lsp_client.start_server().__aenter__()
                    self._lsp_server_active = True
                    logger.info("[LanguageServer] LSP server context started.")
                except Exception as e:
                    logger.error("[LanguageServer] Failed to start LSP server context: {}", e, exc_info=True)
                    self.lsp_client = None
                    self._lsp_server_active = False
                    raise

    async def search_code(self, name: str, file_path: str, line_num: int) -> List[Code]:
        if not Path(file_path).is_relative_to(self.repo_root_path):
            if file_path == str(self.config.harness_path):
                logger.info(f"[LanguageServer] Ignoring search from harness file outside the repo.")
                return []
            else:
                logger.error(f"[LanguageServer] File path {file_path} is not within the repo root {self.repo_root_path}. Aborting search.")
                return []

        try:
            await self.init()
        except Exception as e_init:
            logger.error("[LanguageServer] LSP client initialization failed for search: {}", e_init, exc_info=True)
            return []

        logger.info(f"[LanguageServer] Searching {name} in {file_path}:{line_num}")

        if not self.lsp_client or not self._lsp_server_active:
            logger.error("[LanguageServer] LSP client not available or server context not active. Aborting search.")
            return []

        try:
            with open(file_path, "r") as f:
                file_lines = f.readlines()
                if line_num <= 0 or line_num > len(file_lines):
                    err_msg = f"Line {line_num} is out of bounds for {file_path}."
                    raise LanuageServerArgumentError(err_msg)
                line = file_lines[line_num - 1]
        except FileNotFoundError:
            raise LanuageServerArgumentError(f"File not found: {file_path}")

        matches = list(re.finditer(rf"\b{re.escape(name)}\b", line))
        if not matches:
            raise LanuageServerArgumentError(f"`{name}` was not used in {file_path}:{line_num}:\n```\n{line_num} {line}```")

        codes = {}
        try:
            for m in matches:
                column = m.start() + 1
                logger.info(f"[LanguageServer] Found '{name}' referenced at {file_path}:{line_num}:{column}")
                rel_file_path = Path(file_path).resolve().relative_to(self.repo_root_path)

                try:
                    locs = await self.lsp_client.request_definition(str(rel_file_path), line_num - 1, column - 1)
                    logger.info(f"[LanguageServer] Found {len(locs)} definitions for column {column} for `{name}`")

                    for loc in locs:
                        logger.info(f"[LanguageServer] Processing location: {loc} for {name}")
                        uri = loc.get('uri')
                        if uri and not uri.startswith("file://"):
                            logger.warning(f"[LanguageServer] Unsupported URI scheme in location: {uri}")
                            if uri.startswith("jdt://"):
                                code_loc = CodeLocation(
                                    file_path=loc.get('absolutePath'),
                                    start_line=loc.get('range', {}).get('start', {}).get('line'),
                                    end_line=loc.get('range', {}).get('end', {}).get('line'),
                                )
                                codes[code_loc] = Code(
                                    error=f"`{name}` referenced at `{file_path}:{line_num}` is in `{loc.get('absolutePath')}` and seems to be in a compiled library. DO NOT search it again because you won't find it."
                                )

                            continue

                        try:
                            absolute_path_str = loc.get('absolutePath')
                            if not absolute_path_str:
                                logger.warning(f"[LanguageServer] Location data missing 'absolutePath': {loc}")
                                continue

                            range = loc.get('range')
                            start_line = range.get('start', {}).get('line') + 1
                            end_line = range.get('end', {}).get('line') + 1

                            regex = f"\\b{name}\\b"
                            results: List[RetrievalResult] = (
                                self.ag_tool.search_function_definition(regex, absolute_path_str) +
                                self.ag_tool.search_type_definition(regex, absolute_path_str) +
                                self.ag_tool.search_declaration(regex, absolute_path_str)
                                )
                            for (i, result) in enumerate(results):
                                logger.info(f"[LanguageServer] Found result {i}/{len(results)} for {name}: {result}")
                                if start_line is not None and end_line is not None:
                                    if not (result.line_start <= start_line <= result.line_end and result.line_start <= end_line <= result.line_end):
                                        logger.info(f"[LanguageServer] Definition {result} is out of range ({start_line}, {end_line})")
                                        continue
                                code_loc = CodeLocation(
                                    file_path=absolute_path_str,
                                    start_line=result.line_start,
                                    end_line=result.line_end
                                )
                                if code_loc in codes:
                                    continue
                                code = Code(
                                    name=name,
                                    body=result.code,
                                    location=code_loc,
                                )
                                codes[code_loc] = code
                                logger.info(f"[LanguageServer] Found code: {code}")
                        except FileNotFoundError:
                            logger.warning(f"[LanguageServer] Definition file not found: {loc.get('absolutePath')}")
                        except Exception as e_proc:
                            logger.error(f"[LanguageServer] Error processing location {loc.get('absolutePath')}: {e_proc}")

                except Exception as e_req:
                    logger.error("[LanguageServer] Error requesting definition for {}:{}:{}: {}", rel_file_path, line_num, column, e_req, exc_info=True)
        except Exception as e_ctx:
            logger.error("[LanguageServer] {}", e_ctx, exc_info=True)
        return list(codes.values())

class CodeIndexer:
    def __init__(self, config: ReverserContext):
        self.config = config
        self.harness_path = config.harness_path
        self.code_indexer = mlla.codeindexer.codeindexer.CodeIndexer(config.redis)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        logger.info("[CodeIndexer] Instance created. Async initialization pending.")

    async def init(self):
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    try:
                        logger.info(f"[CodeIndexer] Starting asynchronous initialization for {self.config.cp.name}...")
                        index_paths = get_common_paths(self.config.cp.cp_src_path, self.config.cp.proj_path)
                        await self.code_indexer.build_index(self.config.cp.name, index_paths, self.config.cp.language)
                        self._initialized = True
                        logger.info("[CodeIndexer] Asynchronous initialization complete and index built.")
                    except Exception as e:
                        logger.error("[CodeIndexer] Failed during asynchronous initialization: {}", e, exc_info=True)
        return self._initialized

    async def search_code(self, name:str, class_name: Optional[str]) -> List[Code]:
        if not await self.init():
            logger.error("[CodeIndexer] Search aborted: CodeIndexer is not initialized.")
            return []

        codes: List[Code] = []
        logger.info(f"[CodeIndexer] Searching {name}")
        class_name_tokens = [] if not class_name else re.findall(r"\b\w+\b", class_name)
        try:
            search_results = await self.code_indexer.search_function(name)
        except Exception as e:
            logger.error("[CodeIndexer] Error during search for {}: {}", name, e, exc_info=True)
            return []

        logger.info(f"[CodeIndexer] Found {len(search_results)} results for `{name}`")
        for code in search_results:
            if class_name_tokens:
                signature_tokens = set(re.findall(r"\b\w+\b", code.func_name))
                if any(token not in signature_tokens for token in class_name_tokens):
                    continue
            logger.info(f"[CodeIndexer] Found code from: {code.file_path}")
            code = Code(
                name=code.func_name,
                body=code.func_body,
                location=CodeLocation(
                    file_path=code.file_path,
                    start_line=code.start_line,
                    end_line=code.end_line
                )
            )
            logger.info(f"[CodeIndexer] Found code: {code}")
            codes.append(code)
        harness_codes = [code for code in codes if code.location.file_path == str(self.harness_path)]
        if harness_codes:
            logger.info(f"[CodeIndexer] Prioritizing harness path results for {name}")
            return harness_codes

        logger.info(f"[CodeIndexer] {len(codes)} results remain after class name filtering for `{name}`")
        if len(codes) > 1 and not class_name:
            return codes[:3] + [Code(error=f"{len(codes)} more definitions found for `{name}`, but omitted due to context limit. Provide `class_name` to narrow down the search.")]

        return codes


class CodeFilter:
    # Get info from bin/symbolizer/harness_coverage_runner.py
    def __init__(self, uniafl_conf: Dict) -> None:
        self.uniafl_conf = uniafl_conf
        self.target = Path(uniafl_conf["harness_path"]).name
        self.out_dir = "/coverage-out/"
        self.cov_harness_path = Path(self.out_dir) / self.target

        # to change /src/projname to /src/repo
        self.project_root = os.getenv("CP_PROJ_PATH", "/src")
        self.src_root = os.getenv("CP_SRC_PATH", "/src/repo")
        self.file_path_cache: Dict[str, str] = {}
        if not self.cov_harness_path.exists():
            logger.info(f"[CodeFilter] coverage harness not found, filtering will not be applied.")
            self.line_info = {}
            return
        self.line_info = self.get_compiled_lines()

    def get_coverage_info(self):
        """
        Get coverage information for the target binary using llvm-cov-custom.
        Using an empty.profdata to just collect the coverage data without actual profiling.
        """
        subprocess.run(
            f"touch empty.profraw",
            shell=True,
            check=True,)
        subprocess.run(
            f"llvm-profdata merge -o empty.profdata empty.profraw",
            shell=True,
            check=True,
        )
        shared_libs = subprocess.check_output(
            f"coverage_helper shared_libs -build-dir={self.out_dir} -object={self.target}",
            cwd=self.out_dir,
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        cov_json = subprocess.check_output(
            f"llvm-cov-custom export --skip-branches --skip-expansions --skip-functions "
            f"{shared_libs} "
            f"-object={self.cov_harness_path} -instr-profile=empty.profdata",
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        return cov_json

    @functools.lru_cache(maxsize=4)
    def _walk_directory(self, root_dir: str) -> Dict[str, List[str]]:
        filename_to_path_dict : Dict[str, List[str]]= {}

        for root, _, files in os.walk(root_dir):
            for filename in files:
                if filename not in filename_to_path_dict:
                    filename_to_path_dict[filename] = []
                filename_to_path_dict[filename].append(os.path.join(root, filename))

        return filename_to_path_dict

    def get_new_file_path(self, old_file_path: str, new_project_root: str) -> Optional[str]:
        filename = os.path.basename(old_file_path)
        candidate_paths = self._walk_directory(new_project_root).get(filename, [])

        if not candidate_paths:
            return None

        if len(candidate_paths) == 1:
            return candidate_paths[0]

        old_parts = old_file_path.split(os.sep)
        old_parts.reverse()
        best_match = None
        longest_match_length = 0
        longest_match_candidate_parts_count = 0

        for candidate_path in candidate_paths:
            candidate_parts = candidate_path.split(os.sep)
            candidate_parts.reverse()

            match_length = 0
            while (
                match_length < len(old_parts)
                and match_length < len(candidate_parts)
                and old_parts[match_length] == candidate_parts[match_length]
            ):
                match_length += 1

            if match_length > longest_match_length or (
                match_length == longest_match_length
                and len(candidate_parts) < longest_match_candidate_parts_count
            ):
                longest_match_length = match_length
                best_match = candidate_path
                longest_match_candidate_parts_count = len(candidate_parts)

        return best_match

    def _real_src_path(self, path_from_build: str) -> str:
        if path_from_build in self.file_path_cache:
            return self.file_path_cache[path_from_build]
        ret = self.get_new_file_path(path_from_build, self.project_root)
        if ret != None:
            self.file_path_cache[path_from_build] = ret
            return ret
        ret = self.get_new_file_path(path_from_build, self.src_root)
        if ret != None:
            self.file_path_cache[path_from_build] = ret
            return ret
        return path_from_build

    def get_compiled_lines(self) -> Dict[str, Set[int]]:
        """
        Extracts the lines of code that are actually compiled and executed
        based on the coverage information from llvm-cov-custom.
        """
        def compiled_lines_from_segments(segments) -> Set[int]:
            segs = sorted([s for s in segments if s[0] > 0], key=lambda s: s[0])
            if not segs:
                return set()

            compiled: Set[int] = set()

            for cur, nxt in zip(segs, segs[1:] + [[segs[-1][0] + 1]]):
                line, has_cnt = cur[0], cur[3]
                if has_cnt:  # actual code that coverage is measuring
                    compiled.update(range(line, nxt[0]))

            return compiled

        cov_json = self.get_coverage_info()
        # Parse the coverage JSON to extract file lines
        data = json.loads(cov_json)["data"][0]
        file_lines: Dict[str, Set[int]] ={
            self._real_src_path(f["filename"]): compiled_lines_from_segments(f["segments"])
            for f in data["files"] if f["segments"]
        }
        logger.info(f"[CodeFilter] Extracted {len(file_lines)} files with compiled lines from coverage data.")
        return file_lines

    def filter_unused_lines(self, code: Code) -> Code:
        if code.error:
            return code
        if code.body is None or code.location is None:
            return code
        if self.line_info is None or not self.line_info:
            return code
        path = code.location.file_path
        start_line = code.location.start_line
        included = self.line_info.get(path, set())
        if not included:
            return code

        lines = code.body.splitlines()
        result = lines[:]
        filter_count = 0
        block_start = 0

        directive_re = re.compile(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b")

        def maybe_filter(end_idx: int) -> None:
            nonlocal filter_count
            # First block is always included, as it is the function definition
            if block_start == 0:
                return
            rng = range(start_line + block_start, start_line + end_idx + 1)
            if all(ln not in included for ln in rng):
                for j in range(block_start, end_idx + 1):
                    result[j] = ""
                    filter_count += 1

        for i, line in enumerate(lines):
            if directive_re.match(line.lstrip()):
                if i - 1 >= block_start:
                    maybe_filter(i - 1)
                block_start = i + 1

        if block_start < len(lines):
            maybe_filter(len(lines) - 1)

        filtered_body = "\n".join(result)
        if filter_count > 0:
            logger.info(f"[CodeFilter] Filtered code body for {code.name} in {path}, removed {filter_count} lines.")
        return dataclasses.replace(code, body=filtered_body)

class CodeTool:
    @dataclasses.dataclass(frozen=True)
    class CodeIndex:
        name: str
        file_path: Optional[str]
        line_num: Optional[int]
        class_name: Optional[str]

    def __init__(self, config: ReverserContext, uniafl_conf: Optional[Dict] = None):
        self.cache: Dict[CodeTool.CodeIndex, List[Code]] = defaultdict(list)
        self.harness_path = config.harness_path
        self.code_indexer = CodeIndexer(config)
        self.lsp_helper = LanguageServer(config)
        self.lsp_fails = 0
        self.code_filter = None
        if uniafl_conf:
            try:
                self.code_filter = CodeFilter(uniafl_conf)
                logger.info("[CodeTool] Initialized CodeFilter with uniafl configuration.")
            except Exception as e:
                logger.error("[CodeTool] Failed to load CodeFilter: {}", e, exc_info=True)

        logger.info("[CodeTool] Initialized.")

    def filter_code(self, code: Code) -> Code:
        if self.code_filter:
            return self.code_filter.filter_unused_lines(code)
        return code

    async def search_code(self, name: str, file_path: Optional[str], line_num: Optional[int], class_name: Optional[str], candidates_index: Optional[int]) -> List[Code]:
        code_idx = self.CodeIndex(name, file_path, line_num, class_name)

        if candidates_index is not None:
            if code_idx not in self.cache:
                logger.error(f"Cache miss for index selection: {name} (class: {class_name})")
                return []
            if not isinstance(self.cache[code_idx], list) or candidates_index >= len(self.cache[code_idx]):
                logger.error(f"Invalid candidate index: {candidates_index} for {name}")
                return []
            return [self.cache[code_idx][candidates_index]]

        codes = []
        if (self.lsp_fails < 5) and file_path and line_num:
            try:
                codes = await asyncio.wait_for(
                    self.lsp_helper.search_code(name, file_path, line_num),
                    timeout=30.0
                )
                self.lsp_fails = 0
                logger.info(f"[CodeTool] LSP search finished, found {len(codes)} results for {name}")
            except asyncio.TimeoutError:
                self.lsp_fails += 1
                logger.warning(f"[CodeTool] LSP search timed out (30s, {self.lsp_fails}th) for {name} at {file_path}:{line_num}")
                codes = []
            except LanuageServerArgumentError:
                raise
            except Exception as e:
                logger.error("[CodeTool] LSP search failed for {}: {}", name, e, exc_info=True)

        if not codes:
            logger.info(f"[CodeTool] Falling back to CodeIndexer for {name}")
            try:
                codes = await self.code_indexer.search_code(name, class_name)
                logger.info(f"[CodeTool] CodeIndexer found {len(codes)} results for {name}")
            except Exception as e:
                 logger.error("[CodeTool] CodeIndexer search failed for {}: {}", name, e, exc_info=True)
                 codes = []

            codes = [self.filter_code(code) for code in codes]

        self.cache[code_idx] = codes
        return codes

def create_code_tools(config: ReverserContext) -> List[BaseTool]:
    code_tool = CodeTool(config)

    @tool
    async def search_code(name: str, file_path: str, line_num: int, class_name: Optional[str], candidates_index: Optional[int]) -> str:
        """Search definitions for a function or variable name.
        Provide the name, the file path, and the line number where the name appears.
        If multiple definitions are found, you'll be prompted to call again with a 'candidates_index'.

        Args:
            name (str): The function or variable name to search for.
            file_path (str): The absolute path to the file containing the reference. (Always `/src/repo/...` or the harness path in `<harness>`)
            line_num (int): The line number in the file where the name is referenced.
            class_name (Optional[str]): The class context, if applicable.
            candidates_index (Optional[int]): If prompted, provide the index of the desired definition.
        """
        try:
            results = await code_tool.search_code(name, file_path, line_num, class_name, candidates_index)

            if not results:
                search_term = f"{class_name}.{name}" if class_name else name
                logger.warning(f"Tool: No definitions found for '{search_term}' referenced at {file_path}:{line_num}")
                return f"Error: No definitions found for '{search_term}'. DO NOT search it again because you won't find it."
            elif len(results) == 1:
                return str(results[0])
            else:
                logger.info(f"Tool: Multiple definitions found for {name}. Prompting for index.")
                msg = f"Found {len(results)} definitions for '{name}'. Specify 'candidates_index' from 0 to {len(results)-1} to select one:"
                options = [f"{i}: {code.name} in {Path(code.location.file_path).name}" for i, code in enumerate(results)]
                return "\n".join([msg] + options)

        except Exception as e:
            logger.error("Tool Error: search_code failed - {}", e, exc_info=True)
            return f"Error: An unexpected error occurred during the search: {e}"

    return [search_code]
