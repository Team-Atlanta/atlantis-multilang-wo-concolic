import os
import platform
import re
import shutil
from pathlib import Path

import yaml
from libCRS import util
from libCRS.challenge import CP, CP_Harness
from loguru import logger
from pydantic.dataclasses import dataclass
from typing_extensions import List

# def is_running_in_docker() -> bool:
#     """Check if the current process is running inside a Docker container."""
#     # Check if it's running in the Github Actions environment
#     if os.getenv("GITHUB_ACTIONS", "false").lower() == "true":
#         return False
#     return os.path.exists("/.dockerenv")


def get_docker_gateway() -> str:
    """Get the appropriate Docker host URL based on OS."""
    if platform.system() == "Linux":
        # On Linux, detect the actual Docker gateway
        # Try to get from /proc/net/route
        try:
            with open("/proc/net/route") as f:
                for line in f:
                    fields = line.strip().split()
                    if fields[1] == "00000000":  # Default gateway
                        gateway = fields[2]
                        # Convert from hex to IP address
                        return ".".join(
                            str(int(gateway[i : i + 2], 16)) for i in (6, 4, 2, 0)
                        )
        except (IOError, IndexError):
            pass

        # Last resort - use default with warning
        logger.warning("Could not detect Docker gateway IP, using default 172.17.0.1")
        return "172.17.0.1"
    else:
        # On macOS and Windows, host.docker.internal is available
        return "host.docker.internal"


@dataclass
class sCP_Harness(CP_Harness):
    name: str
    bin_path: Path | None
    src_path: Path


# sCP: serializable CP
@dataclass
class sCP(CP):
    name: str
    proj_path: Path
    cp_src_path: Path
    aixcc_path: Path
    built_path: Path | None
    language: str
    harnesses: dict[str, sCP_Harness]

    def __post_init__(self):
        self._cached_files = None  # Add cache variable

    @property
    def yaml_path(self) -> Path:
        return self.proj_path / "project.yaml"

    @property
    def sanitizers(self) -> List[str]:
        with open(self.yaml_path, "r") as f:
            info = yaml.safe_load(f)
        return info["sanitizers"]

    @property
    def compile_db_json_path(self) -> Path:
        return self.cp_src_path / "compile_commands.json"

    def init_compile_db_json(self) -> None:
        """Process compile commands json file."""
        if not (self.language == "c" or self.language == "c++"):
            return

        compile_commands_json: Path = self.cp_src_path / "compile_commands.json"

        if not compile_commands_json.exists():
            logger.error(
                f"Compile commands json file not found: {compile_commands_json}"
            )
            return

        backup_compile_commands_json = compile_commands_json.with_suffix(".json.bak")

        with open(compile_commands_json, "r") as f:
            orig_data = f.read()

            pattern = r'((?:^|["\s])(?:-I)?)/src/([^/\s"]+)([^\s"]*)'
            new_data = []

            for line in orig_data.splitlines():
                line = re.sub(pattern, f"\\1{self.cp_src_path.as_posix()}\\3", line)
                new_data.append(line)

        if not backup_compile_commands_json.exists():
            with open(backup_compile_commands_json, "w") as fw:
                fw.write(orig_data)
        else:
            logger.info(
                "Backup compile commands json file already exists:"
                f" {backup_compile_commands_json}"
            )

        with open(compile_commands_json, "w") as fw:
            data = "\n".join(new_data)
            fw.write(data)

        logger.info(f"Processed compile commands json file: {compile_commands_json}")

        proj_compile_commands_json = self.proj_path / "compile_commands.json"
        shutil.copy(compile_commands_json, proj_compile_commands_json)

    @staticmethod
    def from_cp_path(cp_path: Path, target_harness: str = "") -> tuple["CP", "sCP"]:
        # Initialize as Optional[Path]
        cp_src_path: Path | None = None

        # 0. Initialize CP based on whether CRS_TARGET exists
        cp_name = os.getenv("CRS_TARGET")
        if cp_name:
            # If CRS_TARGET exists, use it directly
            logger.info(f"Using CRS_TARGET: {cp_name}")
        else:
            # If no CRS_TARGET, use the CP path name
            cp_name = cp_path.name
            logger.info(f"No CRS_TARGET found, using CP path name: {cp_name}")

        # 1. Check CP_SRC_PATH (set by libCRS)
        # https://github.com/Team-Atlanta/libCRS/blob/736c9664891d793d7219e742f1ec4f210f26a3aa/libCRS/crs.py#L65
        cp_src_path_str = os.getenv("CP_SRC_PATH")
        if cp_src_path_str:
            logger.info(f"Using CP_SRC_PATH: {cp_src_path_str}")
            path = Path(cp_src_path_str)
            if path.exists():
                cp_src_path = path
            else:
                logger.warning(f"Env path: {path} does not exist")

        # 2. Check CP_SRC env (set by CRS-multilang) only if CP_SRC_PATH was not valid
        # https://github.com/Team-Atlanta/CRS-multilang/blob/e079521975288cce1a11c0b07b04650f81ae3ccf/run.py#L258
        if not cp_src_path:
            cp_src_path_str = os.getenv("CP_SRC")
            if cp_src_path_str:
                logger.info(f"Using CP_SRC: {cp_src_path_str}")
                path = Path(cp_src_path_str)
                if path.exists():
                    cp_src_path = path
                else:
                    logger.warning(f"Env path: {path} does not exist")

        # 3. Check 'cp_path / repo' (potentially set by libCRS or CRS-multilang)
        # https://github.com/Team-Atlanta/libCRS/blob/oss-fuzz/libCRS/challenge.py#L79
        # https://github.com/Team-Atlanta/CRS-multilang/blob/e079521975288cce1a11c0b07b04650f81ae3ccf/run.py#L178
        if not cp_src_path:
            repo_path = cp_path / "repo"
            if repo_path.exists() and (repo_path / ".git").is_dir():
                logger.info(f"Using existing repo directory: {repo_path}")
                cp_src_path = repo_path
            else:
                logger.info("No existing repo directory found")

        # 4. If all above fail, call init_cp_repo() (we fetch it)
        if not cp_src_path:
            logger.info("Initializing CP repository by fetching")
            cp_src_path = init_cp_repo(cp_path)

        out_path = os.getenv("OUT")

        # Create the actual CP instance
        cp = CP(cp_name, cp_path, cp_src_path, out_path)

        # Create serializable harnesses
        harnesses = {
            name: sCP_Harness(
                harness.name,
                harness.bin_path,
                harness.src_path,
            )
            for name, harness in cp.harnesses.items()
            if not target_harness or harness.name == target_harness
        }

        scp = sCP(
            name=cp.name,
            proj_path=cp_path,
            cp_src_path=cp_src_path,
            aixcc_path=cp.aixcc_path,
            built_path=cp.built_path,
            language=cp.language,
            harnesses=harnesses,
        )

        # Create the serializable CP
        return cp, scp

    def list_files_recursive(self) -> List[Path]:
        # Return cached result if available
        if self._cached_files is not None:
            return self._cached_files

        # If not cached, compute and cache the result
        files = []
        for root, _, filenames in os.walk(self.proj_path):
            for filename in filenames:
                files.append(Path(root) / filename)

        self._cached_files = files  # Cache the result
        return files


# Modified from run.py
# https://github.com/Team-Atlanta/CRS-multilang/blob/main/run.py
# This function is only used for:
# 1. Development purposes outside of Docker
# 2. Fallback when CP_SRC environment variable is not set in Docker (should not happen)
def init_cp_repo(cp_path: Path) -> Path:
    # TODO: This should use tarball instead of git clone.
    """Initialize and return the repository path for a CP"""
    logger.info(f"Initializing CP repository at: {cp_path}")
    project_yaml_path = cp_path / "project.yaml"
    logger.info(f"Loading project configuration from: {project_yaml_path}")

    with open(project_yaml_path) as f:
        proj_yaml = yaml.safe_load(f)

    aixcc_conf = {}
    aixcc_conf_path = cp_path / ".aixcc/config.yaml"
    if not aixcc_conf_path.exists():
        # TODO: we may need to handle this when integration.
        raise FileNotFoundError(f"Config file not found: {aixcc_conf_path}")

    with open(aixcc_conf_path, "r") as f:
        aixcc_conf = yaml.safe_load(f)

    base_commit = aixcc_conf.get("full_mode", {}).get("base_commit", "")
    main_repo = proj_yaml.get("main_repo", "")

    if not base_commit:
        # TODO: we may need to handle this when integration.
        raise ValueError("base_commit is not specified in .aixcc/config.yaml")
    if not main_repo:
        # TODO: we may need to handle this when integration.
        raise ValueError("main_repo is not specified in project.yaml")

    repo_path = cp_path / "repo"
    if not repo_path.exists():
        logger.info(f"Cloning repository from {main_repo} to {repo_path}")
        r = util.run_cmd(["git", "clone", main_repo, str(repo_path)])

    if not repo_path.exists():
        raise FileNotFoundError(f"Failed to clone repository: {r.stderr, r.stdout}")

    # Check if repository was cloned correctly
    git_dir = repo_path / ".git"
    if not git_dir.exists() or not git_dir.is_dir():
        raise RuntimeError(
            f"Failed to clone repository: {repo_path} is not a valid git repository"
        )

    util.run_cmd(["git", "-C", str(repo_path), "checkout", "-f", base_commit])
    logger.info(f"Successfully checked out commit: {base_commit}")

    return repo_path
