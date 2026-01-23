import asyncio
import contextvars
import datetime
import os
import random
import sys

# import getpass
import time
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Queue
from typing import Any, AsyncIterator, Optional

import psutil
from dotenv import load_dotenv
from fuzzdb import FuzzDB
from langchain_community.callbacks.openai_info import OpenAICallbackHandler
from langchain_core.rate_limiters import InMemoryRateLimiter
from libCRS import CP
from loguru import logger
from mljoern.client import JoernClient
from multilspy.language_server import LanguageServer
from multilspy.multilspy_config import MultilspyConfig
from redis import Redis
from tenacity import retry, stop_after_attempt

from mlla.utils.diff_analyzer import DiffAnalyzer

from ..codeindexer.codeindexer import CodeIndexer
from .bedrock_callback import BedrockTokenUsageCallbackHandler
from .coverage import (
    InterestingSeedPolicy,
    InterestingSeedPolicyContext,
    init_fuzzdb,
    load_interesting_seed,
)
from .cp import sCP, sCP_Harness
from .historytracker import HistoryTracker
from .joern_adaptor import check_joern
from .lsp_logger import MLLALspLogger
from .redis_saver import RedisSaver
from .redis_utils import init_redis_with_retry
from .status_bar import run_with_timeout_bar
from .tracer.model import FunctionInfo, MethodInfo, TracerResult


def print_env_vars():
    """Print all environment variables loaded from env files."""
    env_info = []
    env_info.append("Environment Variables Loaded:")

    env_info.append("\n- General Settings:")
    env_info.append(f"  ALLOW_TIMEOUT_BUG: {os.getenv('ALLOW_TIMEOUT_BUG', False)}")

    env_info.append("\n- LLM Settings:")
    env_info.append(
        "  MAX_CONCURRENT_ASYNC_LLM_CALLS:"
        f" {os.getenv('MAX_CONCURRENT_ASYNC_LLM_CALLS')}"
    )

    env_info.append("\n- Model-specific Settings:")
    env_info.append(f"  OPENAI_TIMEOUT: {os.getenv('OPENAI_TIMEOUT')}")
    env_info.append(f"  OPENAI_MAX_RETRIES: {os.getenv('OPENAI_MAX_RETRIES')}")
    env_info.append(f"  GEMINI_TIMEOUT: {os.getenv('GEMINI_TIMEOUT')}")
    env_info.append(f"  GEMINI_MAX_RETRIES: {os.getenv('GEMINI_MAX_RETRIES')}")
    env_info.append(f"  ATLANTA_TIMEOUT: {os.getenv('ATLANTA_TIMEOUT')}")
    env_info.append(f"  ATLANTA_MAX_RETRIES: {os.getenv('ATLANTA_MAX_RETRIES')}")

    env_info.append("\n- MCGA Settings:")
    env_info.append(f"  MCGA_MODEL: {os.getenv('MCGA_MODEL')}")
    env_info.append(
        "  MCGA_SANITIZER_VALIDATOR_MODEL:"
        f" {os.getenv('MCGA_SANITIZER_VALIDATOR_MODEL')}"
    )

    env_info.append("\n- BGA Settings:")
    env_info.append(f"  BGA_MODEL: {os.getenv('BGA_MODEL')}")
    env_info.append(f"  BGA_TEMPERATURE: {os.getenv('BGA_TEMPERATURE')}")
    env_info.append(f"  BGA_MAX_RETRIES: {os.getenv('BGA_MAX_RETRIES')}")
    env_info.append(
        f"  BGA_NUM_INITIAL_PAYLOAD: {os.getenv('BGA_NUM_INITIAL_PAYLOAD')}"
    )
    env_info.append(
        f"  BGA_NUM_FAILURE_PAYLOAD: {os.getenv('BGA_NUM_FAILURE_PAYLOAD')}"
    )
    env_info.append(f"  BGA_MAX_CONCURRENT_CG: {os.getenv('BGA_MAX_CONCURRENT_CG')}")
    env_info.append(
        "  BGA_MAX_CONCURRENT_ASYNC_LLM_CALLS:"
        f" {os.getenv('BGA_MAX_CONCURRENT_ASYNC_LLM_CALLS')}"
    )

    env_info.append("\n- Payload Generation Settings:")
    env_info.append(f"  PAYLOAD_GEN_MODEL: {os.getenv('PAYLOAD_GEN_MODEL')}")
    env_info.append(
        f"  PAYLOAD_GEN_TEMPERATURE: {os.getenv('PAYLOAD_GEN_TEMPERATURE')}"
    )
    env_info.append(
        f"  PAYLOAD_GEN_MAX_RETRIES: {os.getenv('PAYLOAD_GEN_MAX_RETRIES')}"
    )

    env_info.append("\n- Failure Analysis Settings:")
    env_info.append(f"  FAILURE_ANALYSIS_MODEL: {os.getenv('FAILURE_ANALYSIS_MODEL')}")
    env_info.append(
        f"  FAILURE_ANALYSIS_TEMPERATURE: {os.getenv('FAILURE_ANALYSIS_TEMPERATURE')}"
    )
    env_info.append(
        f"  FAILURE_ANALYSIS_MAX_RETRIES: {os.getenv('FAILURE_ANALYSIS_MAX_RETRIES')}"
    )

    # Join all lines and print once
    logger.info("\n".join(env_info))


def kill_uniafl():
    """Kill uniafl process."""

    def kill_process_tree(pid):
        """Kill a process and all its children recursively."""
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)

            # Kill all children first
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass

            # Kill the parent process
            parent.kill()
        except psutil.NoSuchProcess:
            pass

    # Find all Python processes running main.py
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"]
            if cmdline and len(cmdline) > 1:
                if cmdline[0] == "python3" and "/usr/local/bin/main.py" in cmdline[1]:
                    kill_process_tree(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


current_retry_dict: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "current_retry_dict", default={}
)


def log_retry_error(retry_state):
    """Log retry attempts for better visibility."""
    global current_retry_dict
    _current_retry_dict = current_retry_dict.get()
    _current_retry_dict[retry_state.fn.__name__] = retry_state.attempt_number
    current_retry_dict.set(_current_retry_dict)
    exception = retry_state.outcome.exception()
    if exception:
        logger.warning(
            f"Retrying {retry_state.fn.__name__} after error:"
            f" {type(exception).__name__}: {str(exception)}. Attempt"
            f" {retry_state.attempt_number}"
        )
    return True


def retry_on_init_error():
    return retry(
        stop=stop_after_attempt(10),
        before_sleep=log_retry_error,
    )


class GlobalContext:
    api_key: str
    base_url: str
    openai_callback: OpenAICallbackHandler
    general_callback: BedrockTokenUsageCallbackHandler
    global_rate_limiter: InMemoryRateLimiter
    global_claude_rate_limiter: InMemoryRateLimiter
    max_concurrent_async_llm_calls: int
    no_llm: bool
    cur_thread_id: int
    graph_config: dict
    history_tracker: Optional[HistoryTracker]
    cp: sCP
    _cp: CP
    redis: Redis
    checkpointer: RedisSaver | None
    RESULT_DIR: Path
    BLOBS_DIR: Path
    RESULT_FILE: Path
    load_agent_names: list[str]
    is_dev: bool
    redis_host: str | None
    redis_port: int
    crs_multilang_path: Path | None
    code_indexer: CodeIndexer
    _start_time: datetime.datetime
    in_ci: bool
    lsp_server: LanguageServer
    cur_harness: sCP_Harness
    joern_client: JoernClient
    fuzzdb: FuzzDB | None
    recent_tracer_result: TracerResult | None

    def __init__(
        self,
        no_llm: bool,
        cp_path: Path,
        load_agent_names: list[str] = [],
        target_harness: str = "",
        workdir: str = "results",
        output_dir: str | None = None,
        redis_host: str | None = None,
        in_ci: bool = False,
        in_eval: bool = False,
        soyeon_debug: bool = False,
        standalone: bool = False,
    ):
        """Initialize GlobalContext with configuration and setup.

        Args:
            no_llm: Whether to run without LLM
            cp_path: Path to challenge program
            load_agent_names: List of agent names to load
            target_harness: Target harness name
            crs_multilang_path: Path to CRS multilang repository
            workdir: Directory for intermediate results
            output_dir: Directory for generated blobs
            redis_host: Redis server address (default: localhost or docker gateway)
        """
        load_dotenv(".env.secret")

        # Check if this is CI environment
        if os.getenv("TEST_TIMEOUT", False) or in_ci:
            load_dotenv("env.ci")
            logger.info("Loaded Config File: env.ci")
        elif in_eval:
            load_dotenv("env.eval")
            logger.info("Loaded Config File: env.eval")
        else:
            load_dotenv("env.shared")
            logger.info("Loaded Config File: env.shared")

        print_env_vars()

        self.is_dev = True
        self.standalone = standalone
        # Start execution timer
        self._start_time = datetime.datetime.now()
        self.timestamp = self._start_time.strftime("%Y-%m-%d_%H-%M-%S")

        self._init_env_vars()
        self._init_llm(no_llm)

        self._init_cp(cp_path, target_harness)

        # If we need to use checkpointer again,
        # we need to setup redis before this step
        if not standalone:
            self._init_redis(redis_host)
            if soyeon_debug:
                cgpa_keys: Any = self.redis.keys(f"cgpa::{self.cp.name}::*")
                for key in cgpa_keys:
                    self.redis.delete(key)

                mcga_keys: Any = self.redis.keys(
                    f"mcga::{self.cp.name}::{self.cur_harness.name}::*"
                )
                for key in mcga_keys:
                    self.redis.delete(key)
            self._init_mcga_cache_in_run()

        self._init_checkpointer()

        self._init_directories(workdir, output_dir, target_harness)

        # These will be initialized in init() if successful #####
        self.lsp_server = None
        self.joern_client = None
        self.fuzzdb = None
        self.recent_tracer_result = None
        #########################################################
        # This will be initialized in set_candidate_queue()
        self.candidate_queue: Queue | None = None
        #########################################################

        # Other state initialization
        self.load_agent_names = load_agent_names
        self.target_harness = target_harness
        self.in_ci = in_ci
        self.soyeon_debug = soyeon_debug

    async def _test(self, server_cm):
        # await asyncio.sleep(120)
        await server_cm.__aenter__()

    @retry_on_init_error()
    async def _init_lsp(self):
        global current_retry_dict
        lsp_server = self._init_lsp_server()
        server_cm = lsp_server.start_server()
        lsp_server_init_task = asyncio.create_task(self._test(server_cm))
        _current_retry_dict = current_retry_dict.get()
        retry_count = _current_retry_dict.get(self._init_lsp.__name__, 0)
        logger.info(f"Retry count: {retry_count}")
        if retry_count < 3:
            raw_timeout = 60 - retry_count * 20
            timeout = max(10, raw_timeout)
        else:
            timeout = 10

        await run_with_timeout_bar(
            lsp_server_init_task, timeout=timeout, desc="LSP Server"
        )
        self.lsp_server = lsp_server
        logger.info("🟢 Initialized LSP Server")
        return server_cm.__aexit__(None, None, None)

    @retry_on_init_error()
    async def _init_joern(self):
        global current_retry_dict
        joern_url = os.getenv("JOERN_URL")
        if joern_url is not None and ":" not in joern_url:
            joern_url = f"{joern_url}:9909"
            os.environ["JOERN_URL"] = joern_url

        joern_client = JoernClient()
        self.joern_lock = asyncio.Lock()
        joern_server_init_task = asyncio.create_task(
            check_joern(joern_client, self.joern_lock)
        )

        _current_retry_dict = current_retry_dict.get()
        retry_count = _current_retry_dict.get(self._init_joern.__name__, 0)
        if retry_count < 3:
            raw_timeout = 60 - retry_count * 20
            timeout = max(10, raw_timeout)
        else:
            timeout = 10

        await run_with_timeout_bar(
            joern_server_init_task, timeout=timeout, desc="Joern Server"
        )
        self.joern_client = joern_client
        logger.info("🟢 Initialized Joern Server")
        return None

    @asynccontextmanager
    async def init(self) -> AsyncIterator["GlobalContext"]:
        """
        Initialize code indexer, LSP server, Joern server, and tracer result.
        """
        from .agent import BCDA, CPUA

        init_tasks = []
        if CPUA in self.load_agent_names and BCDA in self.load_agent_names:
            init_tasks.append(self._init_code_indexer())
            results = await asyncio.gather(*init_tasks, return_exceptions=True)
            yield self
            return

        init_tasks = []
        init_tasks.append(self._init_lsp())
        init_tasks.append(self._init_code_indexer())

        init_tasks.append(self._init_joern())

        results = await asyncio.gather(*init_tasks, return_exceptions=True)

        cleanup_tasks = []

        for result in results:
            if isinstance(result, asyncio.TimeoutError):
                logger.error(f"Timeout error: {result}")
            elif isinstance(result, Exception):
                import traceback

                tb_lines = traceback.format_exception(
                    type(result), result, result.__traceback__
                )
                logger.error("".join(tb_lines))
            else:
                # logger.info(f"Initialized {result.__class__.__name__}")
                if result is not None:
                    cleanup_tasks.append(result)

        # This needs to be done after LSP server is initialized
        await self._init_diff()
        tracer_result_task = None
        try:
            timeout = int(os.getenv("TRACER_TIMEOUT", "600"))
            await run_with_timeout_bar(
                self.update_tracer_result(), timeout=timeout, desc="Tracer Result"
            )
            tracer_result_task = asyncio.create_task(
                self.update_tracer_result_regularly()
            )

        except asyncio.TimeoutError:
            logger.warning("🟡 Tracer result timed out")

        if self.in_ci:
            kill_uniafl()
            logger.info("🟢 Killed uniafl")

        yield self

        for task in cleanup_tasks:
            await task

        if tracer_result_task:
            logger.info("🟡 Canceling tracer result task")
            tracer_result_task.cancel()
            try:
                await tracer_result_task
            except asyncio.CancelledError:
                logger.info("🟢 Canceled tracer result task")

    def set_candidate_queue(self, candidate_queue: Queue):
        self.candidate_queue = candidate_queue

    def set_cpua_target_fns(
        self,
        target_fns: list[
            tuple[tuple[str, str, str, list[int], tuple[int, int]], list[str]]
        ],
    ):
        self.cpua_target_fns = target_fns

    def get_execution_time(self) -> float:
        # Calculate execution time
        return (datetime.datetime.now() - self._start_time).total_seconds()

    def _init_mcga_cache_in_run(self) -> None:
        from mlla.agents.mcga import MCGAFuncInfo

        mcga_keys: Any = self.redis.keys(
            f"mcga::{self.cp.name}::{self.cur_harness.name}::*"
        )
        for key in mcga_keys:
            node = self.redis.get(key)
            if node is None:
                continue
            mcga_funcinfo = MCGAFuncInfo.model_validate_json(node)
            mcga_funcinfo.in_run = False
            self.redis.set(key, mcga_funcinfo.model_dump_json())

    def _init_lsp_server(self) -> LanguageServer:
        """Initialize LSP server."""
        if self.cp.language == "c" or self.cp.language == "c++":
            language = "c"
        elif self.cp.language == "jvm":
            language = "java"
        else:
            language = self.cp.language

        config = MultilspyConfig.from_dict(
            {
                "code_language": language,
                # TODO: remove this for release
                "trace_lsp_communication": True,
            }
        )
        src_path = self.cp.cp_src_path.resolve().as_posix()
        msp_logger = MLLALspLogger(
            self.workdir.as_posix(), self.cp.name, self.cur_harness.name
        )
        lsp = LanguageServer.create(config, msp_logger, src_path)

        return lsp

    def _init_env_vars(self):
        # Load model-specific settings
        self.openai_timeout = int(os.getenv("OPENAI_TIMEOUT", "120"))
        self.openai_max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "5"))
        self.gemini_timeout = int(os.getenv("GEMINI_TIMEOUT", "120"))
        self.gemini_max_retries = int(os.getenv("GEMINI_MAX_RETRIES", "5"))
        self.atlanta_timeout = int(os.getenv("ATLANTA_TIMEOUT", "120"))
        self.atlanta_max_retries = int(os.getenv("ATLANTA_MAX_RETRIES", "5"))

        # Load general LLM settings
        self.max_concurrent_async_llm_calls = int(
            os.getenv("MAX_CONCURRENT_ASYNC_LLM_CALLS", "20")
        )

    def _init_llm(self, no_llm: bool) -> None:
        """Initialize LLM-related configuration."""
        # Lets print error instead of getting these
        # self.api_key = (
        #     getpass.getpass("Enter your LiteLLM API key: ").strip()
        #     if os.getenv("LITELLM_KEY") is None
        #     else os.getenv("LITELLM_KEY", default="")
        # )
        # self.base_url = (
        #     input("Enter your LiteLLM URL: ").strip()
        #     if os.getenv("LITELLM_URL") is None
        #     else os.getenv("LITELLM_URL", default="")
        # )

        if not os.getenv("LITELLM_KEY"):
            raise ValueError("No LITELLM_KEY is defined")

        if not os.getenv("LITELLM_URL"):
            raise ValueError("No LITELLM_URL is defined")

        self.api_key = os.getenv("LITELLM_KEY", "")
        self.base_url = os.getenv("LITELLM_URL", "")

        self.openai_callback = OpenAICallbackHandler()
        self.general_callback = BedrockTokenUsageCallbackHandler()
        self.no_llm = no_llm

        # Initialize global rate limiters
        self.global_rate_limiter = InMemoryRateLimiter(
            requests_per_second=self.max_concurrent_async_llm_calls,
            check_every_n_seconds=0.3,
            max_bucket_size=self.max_concurrent_async_llm_calls,
        )

        self.global_claude_rate_limiter = InMemoryRateLimiter(
            requests_per_second=5,
            check_every_n_seconds=0.3,
            max_bucket_size=5,
        )

    def _init_checkpointer(self) -> None:
        """Initialize thread and graph configuration."""
        self.cur_thread_id = random.randint(0, sys.maxsize)
        self.graph_config = {
            "configurable": {"thread_id": str(self.cur_thread_id)},
            "recursion_limit": 1000,
        }
        self.history_tracker = None
        self.checkpointer = None
        # self.checkpointer = RedisSaver(self.redis)

    def _init_cp(self, cp_path: Path, target_harness: str) -> None:
        """Initialize challenge program."""
        self._cp, self.cp = sCP.from_cp_path(cp_path, target_harness)
        if target_harness:
            self.cur_harness = self.cp.harnesses[target_harness]
        else:
            self.cur_harness = list(self.cp.harnesses.values())[0]

    def _init_redis(self, redis_host: str | None) -> None:
        """Initialize Redis connection."""
        redis = init_redis_with_retry(redis_host)
        if not redis:
            raise RuntimeError(f"Redis is not set properly for {redis_host}")

        self.redis = redis
        host = self.redis.connection_pool.connection_kwargs["host"]
        port = self.redis.connection_pool.connection_kwargs["port"]
        self.redis_host = f"{host}:{port}"

    async def _init_code_indexer(self) -> None:
        """Initialize code indexer and indexing the project."""
        self.code_indexer = CodeIndexer(self.redis)
        if self.is_dev:
            index_paths = get_common_paths(self.cp.proj_path, self.cp.cp_src_path)
            await self.code_indexer.build_index(
                self.cp.name,
                index_paths,
                self.cp.language,
            )
        else:
            self.code_indexer.setup_project(self.cp.name)
        logger.info("🟢 Initialized Code Indexer")

    def _init_directories(
        self, workdir: str, output_dir: str | None, target_harness: str
    ) -> None:
        """Initialize result and blob directories."""
        # Setup workdir
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        # Setup output_dir if provided
        self.output_dir = Path(output_dir) if output_dir else None
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        # Setup result directory
        result_name = (
            f"{self.cp.name}-{target_harness}" if target_harness else self.cp.name
        )
        if self.standalone:
            result_name += "-standalone"

        self.RESULT_DIR = self.workdir / result_name
        self.RESULT_DIR.mkdir(parents=True, exist_ok=True)

        # Setup blobs directory (always under RESULT_DIR for intermediate results)
        self.BLOBS_DIR = self.RESULT_DIR / "blobs"
        self.BLOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.BLOBS_TIMESTAMP_DIR = self.BLOBS_DIR / self.timestamp
        self.BLOBS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)
        self.CRASH_DIR = self.RESULT_DIR / "crashed_blobs"
        self.CRASH_DIR.mkdir(parents=True, exist_ok=True)
        self.CRASH_TIMESTAMP_DIR = self.CRASH_DIR / self.timestamp
        self.CRASH_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)

        # Setup generators directory for storing generator code
        self.GENERATORS_DIR = self.RESULT_DIR / "generators"
        self.GENERATORS_DIR.mkdir(parents=True, exist_ok=True)
        self.GENERATORS_TIMESTAMP_DIR = self.GENERATORS_DIR / self.timestamp
        self.GENERATORS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)

        # Setup mutators directory for storing generator code
        self.MUTATORS_DIR = self.RESULT_DIR / "mutators"
        self.MUTATORS_DIR.mkdir(parents=True, exist_ok=True)
        self.MUTATORS_TIMESTAMP_DIR = self.MUTATORS_DIR / self.timestamp
        self.MUTATORS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)

        self.BIT_OUTPUT_DIR = None
        self.BLOBS_OUTPUT_DIR = None
        self.GENERATORS_OUTPUT_DIR = None
        self.MUTATORS_OUTPUT_DIR = None
        if self.output_dir:
            self.BIT_OUTPUT_DIR = self.output_dir / "bcda"
            self.BIT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            self.BLOBS_OUTPUT_DIR = self.output_dir / "blobs"
            self.BLOBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            self.GENERATORS_OUTPUT_DIR = self.output_dir / "generators"
            self.GENERATORS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            self.MUTATORS_OUTPUT_DIR = self.output_dir / "mutators"
            self.MUTATORS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Setup result file
        self.RESULT_FILE = self.RESULT_DIR / f"mlla-result-{self.timestamp}.yaml"
        self.LOG_FILE = self.RESULT_DIR / f"mlla-log-{self.timestamp}.log"

    async def _init_diff(self) -> None:
        """Initialize diff analyzer and analyze the diff if it exists."""
        if self._cp.diff_path:
            logger.info("🟢 Analyzing ref.diff....")
            diff_analyzer = DiffAnalyzer(
                str(self._cp.cp_src_path), str(self._cp.diff_path), "", self.lsp_server
            )
            function_diffs = await diff_analyzer.analyze_diff()
            self.function_diffs = function_diffs
        else:
            logger.info("🟡 No ref.diff found")
            self.function_diffs = {}

    async def update_tracer_result_regularly(self) -> None:
        """Update tracer result regularly."""
        idx = 1
        timeout = int(os.getenv("TRACER_TIMEOUT", "600"))
        tracer_result_duration = int(os.getenv("TRACER_RESULT_DURATION", "300"))
        while True:
            await asyncio.sleep(tracer_result_duration)
            try:
                await run_with_timeout_bar(
                    self.update_tracer_result(first_run=False),
                    timeout=timeout,
                    desc=f"Tracer Result-{idx}",
                )
                tracer_result_duration *= 2
            except asyncio.TimeoutError:
                logger.warning(f"Tracer Result-{idx} timed out")
            idx += 1

    async def update_tracer_result(
        self,
        policy_ctx: Optional[InterestingSeedPolicyContext] = None,
        first_run: bool = True,
    ) -> None:
        """Initialize tracer result."""
        from .tracer.func_tracer_adaptor import trace_pov

        logger.info("⭐️ Updating Tracer Result Started")

        if self.fuzzdb is None:
            try:
                self.fuzzdb = await run_with_timeout_bar(
                    init_fuzzdb(self.cur_harness.name),
                    timeout=60,
                    desc="FuzzDB Init",
                )
            except asyncio.TimeoutError:
                logger.warning("FuzzDB timed out")
                return

            logger.info(f"🟢 Initialized FuzzDB for {self.cur_harness.name}")

        if policy_ctx is None:
            policy_ctx = InterestingSeedPolicyContext(
                InterestingSeedPolicy.FUNCTION_COUNT
            )

        timeout = int(os.getenv("TRACER_TIMEOUT", "600"))

        async def _trace_pov():
            res = load_interesting_seed(
                self.fuzzdb,
                policy_ctx,
            )

            if res is None:
                logger.warning("No interesting seed found")
                return
            seed, _cov = res
            try:
                self.recent_tracer_result = await run_with_timeout_bar(
                    trace_pov(self, seed),
                    timeout=timeout,
                    desc="Trace POV",
                )
            except Exception as e:
                logger.error(f"Error tracing POV: {e}")

        first_run_task = asyncio.create_task(_trace_pov())
        second_run_task = None

        try:

            start_time = time.time()

            while not first_run_task.done() and time.time() - start_time < 60 * 3:
                await asyncio.sleep(1)

            # If first task is not done, start second task
            if not first_run_task.done():
                second_run_task = asyncio.create_task(_trace_pov())

                # Wait for either task to complete
                done, pending = await asyncio.wait(
                    [first_run_task, second_run_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel the other task
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            else:
                # First task completed within 1 minute
                await first_run_task

        except Exception as e:
            logger.error(f"Error in parallel trace execution: {e}")
            # Ensure first task runs for at least 10 minutes
            if not first_run_task.done():
                try:
                    await asyncio.wait_for(first_run_task, timeout=600)
                except asyncio.TimeoutError:
                    first_run_task.cancel()
                    await first_run_task

        if not self.recent_tracer_result:
            logger.warning("🟡 No tracer result found")
            return

        if first_run:
            logger.info("🔵 Recent Tracer CG:")
            for caller, callees in self.recent_tracer_result.call_graph.items():
                if isinstance(caller, FunctionInfo):
                    logger.info(f"  {caller.function_name} -> ")
                elif isinstance(caller, MethodInfo):
                    logger.info(f"  {caller.class_name}. {caller.method_name} -> ")
                for cs in callees:
                    if isinstance(cs.callee, FunctionInfo):
                        logger.info(
                            f"    [{cs.file}:{cs.line}] {cs.callee.function_name}"
                        )
                    elif isinstance(cs.callee, MethodInfo):
                        logger.info(
                            f"    {cs.callee.class_name}. {cs.callee.method_name}"
                        )

    def register_history_tracker(self, graph) -> None:
        """Register history tracker for the graph."""
        try:
            self.history_tracker = HistoryTracker(self.graph_config, graph, self.redis)
        except Exception as e:
            logger.info(f"Failed to register history tracker: {e}")

    def get_state(self):
        """Get current state from history tracker."""
        if self.history_tracker is None:
            raise RuntimeError(
                "History tracker not registered. You need to run without"
                " --no-llm once to register the history tracker."
            )
        snapshot = self.history_tracker.get_cur_snapshot()
        return snapshot.values

    def get_sanitizer_type(self) -> list[str]:
        """Get sanitizer list."""
        sanitizer_list = self.cp.sanitizers
        sanitizer_list = ["jazzer"] if self.cp.language == "jvm" else sanitizer_list
        return sanitizer_list

    def finalize(self) -> None:
        """Finalize context and cleanup resources."""
        return
        # if hasattr(self, "_codeindexer_loop"):
        #     # Wait for indexing to complete with timeout
        #     if hasattr(self, "_codeindexer_complete"):
        #         self._codeindexer_complete.wait(
        #             timeout=10
        #         )  # Wait up to 10 seconds for indexing

        #     # Stop the loop if it's still running
        #     if self._codeindexer_loop.is_running():
        #         self._codeindexer_loop.call_soon_threadsafe(self._codeindexer_loop.stop)

        #     # Wait for thread to finish with timeout
        #     if self._codeindexer_thread and self._codeindexer_thread.is_alive():
        #         self._codeindexer_thread.join(timeout=5)

        # try:
        #     repo = Repo(".")
        #     commit_hash = repo.head.commit.hexsha
        # except InvalidGitRepositoryError:
        #     # If Git repository is not available (e.g., in Docker),
        #     # use a placeholder
        #     commit_hash = "no-git"
        #     logger.warning("Git repository not found, using placeholder commit hash")

        # cur_state = {
        #     "thread_id": str(self.cur_thread_id),
        #     "commit_hash": commit_hash,
        #     "timestamp": self.timestamp,
        # }

        # self.redis.hset(f"thread_id:{self.cur_thread_id}", mapping=cur_state)
        # serialized_config = json.dumps(self.graph_config)
        # self.redis.set("latest_graph_config", serialized_config)


def get_common_paths(proj_path: Path, src_path: Path) -> list[Path]:
    """Get index paths based on common parent directories."""
    # Get the parts of both paths
    proj_parts = proj_path.parts
    src_parts = src_path.parts

    # Find common parent directory
    common_length = 0
    for i in range(min(len(proj_parts), len(src_parts))):
        if proj_parts[i] != src_parts[i]:
            break
        common_length = i + 1

    # If they only share root (/) or nothing, return both paths
    if common_length <= 1:
        return [proj_path, src_path]
    else:
        # They share a meaningful common parent
        common_parent = Path(*proj_parts[:common_length])
        return [common_parent]


def detect_crs_multilang_path(current: Path) -> Optional[Path]:
    """Try to detect CRS-multilang path if we're in the repo or a submodule."""
    # Check if we're in CRS-multilang repo
    while current != current.parent:
        if (
            (current / "Dockerfile").exists()
            and (current / "run.py").exists()
            and (current / "blob-gen").exists()
        ):
            return current
        current = current.parent
    return None
