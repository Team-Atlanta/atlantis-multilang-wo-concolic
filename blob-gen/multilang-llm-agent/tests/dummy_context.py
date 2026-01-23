import datetime
import getpass
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.rate_limiters import InMemoryRateLimiter

from mlla.utils.bedrock_callback import BedrockTokenUsageCallbackHandler
from mlla.utils.context import GlobalContext
from mlla.utils.cp import sCP, sCP_Harness


class DummyHarness(sCP_Harness):
    def __init__(self):
        self.src_path = Path("dummy_src")
        self.bin_path = Path("dummy_bin")
        self.language = "python"
        self.name = "dummy"


# Define Dummy configuration
class DummyCP(sCP):
    def __init__(self):
        self.name = "dummy"
        self.proj_path = Path("dummy_proj")
        self.cp_src_path = Path("dummy_src")
        self.aixcc_path = Path("dummy_aixcc")
        self.built_path = Path("dummy_built")
        self.language = "python"
        self.harnesses = {"dummy": DummyHarness()}


class DummyJvmCP(DummyCP):
    def __init__(self):
        super().__init__()
        self.language = "jvm"


class DummyConfig:
    RESULT_DIR = Path("dummy_results")
    cp = DummyCP()


class DummyContext(GlobalContext):
    def __init__(
        self,
        no_llm: bool = False,
        language: str = "python",
        scp: Optional[sCP] = None,
        redis_host: str = "localhost:6379",
    ):
        # Load environment variables
        load_dotenv("env.shared")
        if Path(".env.secret").exists():
            load_dotenv(".env.secret")

        self.api_key = (
            getpass.getpass("Enter your LiteLLM API key: ").strip()
            if os.environ.get("LITELLM_KEY") is None
            else os.environ["LITELLM_KEY"]
        )
        self.base_url = (
            input("Enter your LiteLLM URL: ").strip()
            if os.environ.get("LITELLM_URL") is None
            else os.environ["LITELLM_URL"]
        )
        self.general_callback = BedrockTokenUsageCallbackHandler()
        self.no_llm = no_llm
        self.load_agent_names = []

        self.crs_multilang_path = None
        self.is_dev = True
        self._start_time = datetime.datetime.now()
        self.timestamp = self._start_time.strftime("%Y-%m-%d_%H-%M-%S")
        self.model_name = "gpt-4.1-nano"
        self.temperature = 0.0

        # Add timeout and retry settings
        self.openai_timeout = 120
        self.openai_max_retries = 5
        self.gemini_timeout = 120
        self.gemini_max_retries = 5
        self.atlanta_timeout = 120
        self.atlanta_max_retries = 5
        self.max_concurrent_async_llm_calls = 20

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

        # Create temporary directory for results
        self._temp_dir = tempfile.mkdtemp(prefix="dummycontexttest_")

        # Override some attributes for testing
        self.RESULT_DIR = Path(self._temp_dir)
        self.RESULT_DIR.mkdir(parents=True, exist_ok=True)

        # Setup blobs directory (always under RESULT_DIR for intermediate results)
        self.BLOBS_DIR = self.RESULT_DIR / "blobs"
        self.BLOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.BLOBS_TIMESTAMP_DIR = self.BLOBS_DIR / self.timestamp
        self.BLOBS_TIMESTAMP_DIR.mkdir(parents=True, exist_ok=True)
        self.BLOBS_OUTPUT_DIR = None
        self.BIT_OUTPUT_DIR = None

        self.lsp_server = None
        self.joern_client = None
        self.fuzzdb = None
        self.recent_tracer_result = None
        self.in_ci = True

        if scp is None:
            if language == "jvm":
                self.cp = DummyJvmCP()
            else:
                self.cp = DummyCP()
        else:
            self.cp = scp

        self.workdir = Path(self._temp_dir)
        self.cur_harness = list(self.cp.harnesses.values())[0]
        self.target_harness = self.cur_harness.name
        self._init_lsp_server()
        self._init_redis(redis_host)
        self.joern_client = None
        self.graph_config = {
            "configurable": {"thread_id": str(1)},
            "recursion_limit": 1000,
        }

    def cleanup(self):
        """Remove temporary directory and its contents."""
        if hasattr(self, "_temp_dir") and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()
