import os
from libCRS.challenge import CP
from pathlib import Path
from loguru import logger
from mlla.utils.redis_utils import init_redis_with_retry


class ReverserContext:
    def __init__(
        self,
        config_path: Path,
        workdir: Path,
        codegen_dir: Path,
        harness_path: Path,
        outputs: Path,
        lock: Path,
        max_bytes_size: int,
        base_directory: Path,
        corpus_map: Path | None = None,
        used_testlangs: Path | None = None,
        used_testlangs_timeout: int = 600,
        deprioritized_testlangs: Path | None = None,
        pov_dir: Path | None = None,
        visualize_output: bool = False,
    ):
        self.workdir = workdir
        self.codegen_dir = codegen_dir

        self.harness_path = harness_path
        self.config_path = config_path
        self.corpus_map = corpus_map
        self.pov_dir = pov_dir
        self.base_directory = base_directory
        self.outputs = outputs
        self.lock = lock
        self.used_testlangs = used_testlangs
        self.used_testlangs_timeout = used_testlangs_timeout
        self.deprioritized_testlangs = deprioritized_testlangs

        self.diff_dir = self.workdir / "diffs"
        os.makedirs(self.diff_dir, exist_ok=True)

        self.max_bytes_size = max_bytes_size

        self.visualize_output = visualize_output

        self.api_key = os.getenv("LITELLM_KEY")
        if self.api_key is None:
            raise ValueError("Please provide a LiteLLM API key")

        self.base_url = os.getenv("LITELLM_URL")
        if self.base_url is None:
            raise ValueError("Please provide a LiteLLM URL")

        self.project_name = os.getenv("TARGET_CP")
        if self.project_name is None:
            raise ValueError("Please provide a target CP")

        self.cp_path = os.getenv("CP_PROJ_PATH")
        if self.cp_path is None:
            logger.debug(
                "CP_PROJ_PATH not set, using parent directory of base directory"
            )
            self.cp_path = str(self.base_directory.parent)

        self.cp_src_path = os.getenv("CP_SRC_PATH")
        if self.cp_src_path is None:
            logger.debug("CP_SRC_PATH not set, using base directory")
            self.cp_src_path = str(self.base_directory)

        self.cp = CP(self.project_name, self.cp_path, self.cp_src_path, None)

        redis_url = os.getenv("CODE_INDEXER_REDIS_URL")
        if redis_url is None:
            raise ValueError("Please provide a Code Indexer Redis URL")
        self.redis = init_redis_with_retry(redis_host=redis_url)
