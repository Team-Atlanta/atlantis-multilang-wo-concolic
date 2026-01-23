import asyncio
import atexit
import contextvars
import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Coroutine, Generator, NamedTuple, cast

import pytest
import pytest_asyncio
import tokencost
from loguru import logger
from loguru import logger as real_logger
from redis import Redis

from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.utils.context import GlobalContext
from mlla.utils.cp import sCP
from mlla.utils.redis_utils import init_redis_with_retry
from tests.setup_joern import SetupJoernDocker
from tests.setup_lsp import SetupLSPDocker


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--cp",
        action="store",
        default=None,
        help="Specify the CP name (e.g., aixcc/jvm/jenkins).",
    )
    parser.addoption(
        "--crs-multilang-path",
        action="store",
        default=None,
        help="Specify the CRS multilang path.",
    )
    parser.addoption(
        "--ci",
        action="store_true",
        default=False,
        help="Run in CI mode with automatic LSP server setup.",
    )


@pytest.fixture
def update_tokencost():
    try:
        json_path = (
            Path(os.path.dirname(__file__))
            / ".."
            / "mlla"
            / "assets"
            / "model_prices_and_context_window.json"
        )
        if json_path.exists():
            with open(json_path) as f:
                data = json.load(f)
            tokencost.TOKEN_COSTS.update(data)
    except Exception as e:
        logger.warning(f"Error updating token costs: {e}")


@pytest.fixture
def cp_jenkins_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/jvm/jenkins"


@pytest.fixture
def cp_jackson_databind_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/jvm/jackson-databind"


@pytest.fixture
def cp_batik_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/jvm/batik"


@pytest.fixture
def cp_libpng_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/cpp/example-libpng"


@pytest.fixture
def cp_zstd_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/cpp/zstd-16541"


@pytest.fixture
def cp_nginx_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/c/asc-nginx"


@pytest.fixture
def oss_fuzz_workdir(crs_multilang_path) -> Path:
    return crs_multilang_path / "libs/oss-fuzz/build/work"


@pytest.fixture
def cp_mockc_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/c/mock-c"


@pytest.fixture
def cp_mockjava_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/jvm/mock-java"


@pytest.fixture
def cp_babynginx_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/c/babynginx"


@pytest.fixture
def cp_r3_sqlite3_path(crs_multilang_path) -> Path:
    return crs_multilang_path / "benchmarks/projects/aixcc/c/r3-sqlite3"


@pytest.fixture(scope="session")
def crs_multilang_path(request) -> Path:
    path = request.config.getoption("--crs-multilang-path")
    if path is None:
        pytest.skip("CRS multilang path is not specified, skipping the test.")
    return Path(path)


def _redis_host() -> str:
    from mlla.utils.redis_utils import get_default_redis_host

    if os.getenv("GITHUB_ACTIONS", "false").lower() == "true":
        # If we are using GITHUB_ACTIONS, we will use CRS-multilang redis
        return get_default_redis_host()

    else:
        # Support localhost testing before submitting to github.
        return "localhost:6379"
        # host = os.getenv("REDIS_HOST", "localhost")
        # port = int(os.getenv("REDIS_PORT", "6379"))


@pytest.fixture(scope="session", autouse=True)
def redis_host() -> str:
    """Get Redis host with port based on environment."""
    return _redis_host()


@pytest.fixture(scope="session", autouse=True)
def redis_client(redis_host) -> Redis:
    """Get Redis client using host from redis_host fixture."""
    from mlla.utils.redis_utils import init_redis_with_retry

    # Use db=1 for testing
    client = init_redis_with_retry(redis_host=redis_host, db=1)

    return client


class RedisInfo(NamedTuple):
    host: str  # Connection string (e.g. "localhost:16379")
    container: str  # Container name for docker commands


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisInfo, None, None]:
    """Start a Redis container for testing."""
    # Generate random names
    container_name = uuid.uuid4().hex[:8]
    host_port = 16379 + random.randint(0, 1000)
    # Use a non-standard port to avoid conflicts

    retry_count = 0

    while retry_count < 3:
        try:
            # Start Redis container with port mapping
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-p",
                    f"{host_port}:6379",
                    "redis:latest",
                ],
                check=True,
            )

            # Wait a bit for container to start
            time.sleep(2)
            break
        except Exception as e:
            if retry_count == 2:
                raise e
            logger.error(f"Error starting Redis container: {e}")
            retry_count += 1
            time.sleep(2)
            host_port = 16379 + random.randint(0, 1000)
            container_name = uuid.uuid4().hex[:8]

    try:
        yield RedisInfo(host=f"localhost:{host_port}", container=container_name)
    finally:
        # Cleanup
        subprocess.run(["docker", "stop", container_name], check=True)
        subprocess.run(["docker", "rm", container_name], check=True)


@pytest.fixture
def code_indexer(redis_client) -> CodeIndexer:
    return CodeIndexer(redis_client)


@pytest.fixture
def random_project_name():
    # As we use redis, we need to store each project uniquely.
    return f"test-proj-{uuid.uuid4()}"


@pytest.fixture
def graph_config() -> dict:
    cur_thread_id = random.randint(0, sys.maxsize)
    graph_config = {
        "configurable": {"thread_id": str(cur_thread_id)},
        # TODO: can we set recursion limit per agent?
        "recursion_limit": 1000,
    }
    return graph_config


@pytest.fixture
def config(cp_jenkins_path, crs_multilang_path: Path, redis_host) -> GlobalContext:
    config = GlobalContext(
        False,
        cp_jenkins_path,
        load_agent_names=[],
        redis_host=redis_host,
    )
    return config


@pytest.fixture
def mock_c_cp(cp_mockc_path):
    cp, scp = sCP.from_cp_path(cp_mockc_path, "")
    return scp


@pytest.fixture
def jenkins_cp(cp_jenkins_path):
    cp, scp = sCP.from_cp_path(cp_jenkins_path, "")
    return scp


@pytest.fixture
def babynginx_cp(cp_babynginx_path):
    cp, scp = sCP.from_cp_path(cp_babynginx_path, "")
    return scp


@pytest.fixture
def r3_sqlite3_cp(cp_r3_sqlite3_path):
    cp, scp = sCP.from_cp_path(cp_r3_sqlite3_path, "")
    return scp


lsp_states = {}


# @pytest_asyncio.fixture(scope="session", autouse=True)
@asynccontextmanager
async def setup_lsp_ci(crs_multilang_path, config) -> AsyncGenerator[dict, None]:
    global lsp_states
    # Only run if --ci was given
    if not config.getoption("--ci"):
        # yield an empty state (or None) and do nothing else
        yield {}
        return

    targets = [
        "aixcc/c/babynginx",
        "aixcc/jvm/jenkins",
        "aixcc/c/mock-c",
        "aixcc/jvm/mock-java",
    ]

    async with _setup_lsp(crs_multilang_path, targets) as state:
        lsp_states = state
        yield lsp_states


@pytest_asyncio.fixture
async def setup_lsp(crs_multilang_path, request) -> AsyncGenerator[dict, None]:
    global lsp_states

    if request.config.getoption("--ci"):
        # yield an empty state (or None) and do nothing else
        logger.info("CI mode, skipping LSP setup...")
        yield lsp_states
        return

    state = {}
    logger.info("Setting up LSP servers...")

    # Get target from test function's parametrize or use default targets
    target = getattr(request, "param", None)
    if target is None:
        logger.warning("No target specified for LSP setup, skipping...")
        yield {}
        return
    else:
        targets = [target] if isinstance(target, str) else target

    async with _setup_lsp(crs_multilang_path, targets) as state:
        yield state


@asynccontextmanager
async def _setup_lsp(crs_multilang_path, targets=[]) -> AsyncGenerator[dict, None]:
    state = {}
    logger.info("Setting up LSP servers...")

    tasks = []
    contexts = []
    for target in targets:
        ctx = SetupLSPDocker(crs_multilang_path, target).setup()
        task = asyncio.create_task(ctx.__aenter__())
        tasks.append(task)
        contexts.append(ctx)
        await asyncio.sleep(1)

    # Wait for all LSP servers to start
    results = await asyncio.gather(*tasks)
    for target, result in zip(targets, results):
        state[target] = result

    logger.info("LSP servers setup completed")
    yield state

    logger.info("Cleaning up LSP servers...")
    # Cleanup all LSP servers
    cleanup_tasks = []
    for ctx in contexts:
        coro = cast(Coroutine[Any, Any, Any], ctx.__aexit__(None, None, None))
        task = asyncio.create_task(coro)
        cleanup_tasks.append(task)
    await asyncio.gather(*cleanup_tasks)
    logger.info("LSP servers cleanup completed")


joern_states = {}


# @pytest_asyncio.fixture(scope="session", autouse=True)
@asynccontextmanager
async def setup_joern_ci(crs_multilang_path, request) -> AsyncGenerator[dict, None]:
    global joern_states
    # Only run if --ci was given
    if not request.config.getoption("--ci"):
        # yield an empty state (or None) and do nothing else
        yield {}
        return

    if hasattr(request.config, "workerinput"):
        worker_id = request.config.workerinput["workerid"]
        if worker_id != "gw0":
            yield {}
            return

    print("✅ Running once in gw0")

    targets = [
        "aixcc/c/babynginx",
        "aixcc/jvm/jenkins",
        "aixcc/c/mock-c",
        "aixcc/jvm/mock-java",
    ]

    async with _setup_joern(crs_multilang_path, targets) as state:
        joern_states = state
        yield joern_states


@pytest_asyncio.fixture
async def setup_joern(
    crs_multilang_path, request, direct_param=[]
) -> AsyncGenerator[dict, None]:
    global joern_states

    if request.config.getoption("--ci"):
        # yield an empty state (or None) and do nothing else
        logger.info("CI mode, skipping Joern setup...")
        yield joern_states
        return

    state = {}
    logger.info("Setting up Joern servers...")

    # Get target from test function's parametrize or use default targets
    target = getattr(request, "param", None)
    if target is None:
        if not direct_param:
            logger.warning("No target specified for Joern setup, skipping...")
            yield {}
            return
        else:
            targets = direct_param
    else:
        targets = [target] if isinstance(target, str) else target

    async with _setup_joern(crs_multilang_path, targets) as state:
        yield state


@asynccontextmanager
async def _setup_joern(crs_multilang_path, targets=[]) -> AsyncGenerator[dict, None]:
    state = {}
    logger.info("Setting up Joern servers...")

    tasks = []
    contexts = []
    for target in targets:
        ctx = SetupJoernDocker(crs_multilang_path, target).setup()
        task = asyncio.create_task(ctx.__aenter__())
        tasks.append(task)
        contexts.append(ctx)
        await asyncio.sleep(1)

    # Wait for all LSP servers to start
    results = await asyncio.gather(*tasks)
    for target, result in zip(targets, results):
        state[target] = result

    logger.info("Joern servers setup completed")
    yield state

    logger.info("Cleaning up Joern servers...")
    # Cleanup all LSP servers
    cleanup_tasks: list[asyncio.Task[Any]] = []
    for ctx in contexts:
        coro = cast(Coroutine[Any, Any, Any], ctx.__aexit__(None, None, None))
        task = asyncio.create_task(coro)
        cleanup_tasks.append(task)
    await asyncio.gather(*cleanup_tasks)
    logger.info("Joern servers cleanup completed")


# Setup Logger for pytest #######################################################


# Store test name in context
test_name_var = contextvars.ContextVar("test_name", default="unknown")

real_logger.remove()
real_logger.add(
    sys.stderr,
    format=(
        "<cyan>{extra[test_name]}</cyan> │ "
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> │ "
        "<level>{level: <8}</level> │ "
        "<blue>{module}:{function}:{line}</blue> │ "
        "<level>{message}</level>"
    ),
    colorize=True,
)


# monkey-patch: loguru's global logger to automatically apply bind
def context_logger_wrapper(method_name):
    def wrapper(*args, **kwargs):
        bound = real_logger.bind(test_name=test_name_var.get(), depth=1)
        return getattr(bound.opt(depth=1), method_name)(*args, **kwargs)

    return wrapper


# monkey-patch: patch all methods of loguru.logger
for method in ["debug", "info", "warning", "error", "critical", "exception", "log"]:
    setattr(real_logger, method, context_logger_wrapper(method))


# autouse fixture: set test name
@pytest.fixture(autouse=True)
def inject_test_name(request):
    test_name_var.set(request.node.name)


setup_loop = None
setup_thread = None
setup_context = {}
setup_ready_flag = Path("/tmp/setup_ready.flag")
lsp_states_file = Path("/tmp/lsp_states.json")
joern_states_file = Path("/tmp/joern_states.json")


def wait_and_set_states(timeout=60 * 5, poll_interval=0.2):
    global lsp_states, joern_states
    start = time.time()
    while not setup_ready_flag.exists():
        if time.time() - start > timeout:
            raise RuntimeError(
                "Timed out waiting for LSP and Joern setup (flag not found)."
            )
        time.sleep(poll_interval)

    if not lsp_states_file.exists() or not joern_states_file.exists():
        raise RuntimeError(
            "LSP and Joern setup is not complete (flag found but files not found)."
        )
    lsp_states = json.loads(lsp_states_file.read_text())
    joern_states = json.loads(joern_states_file.read_text())


def pytest_configure(config):
    global setup_context, setup_thread

    if not config.getoption("--ci"):
        return

    timeout = 60 * 60  # seconds
    poll_interval = 0.2

    if hasattr(config, "workerinput"):
        worker_id = config.workerinput["workerid"]
        # This is a worker process
        logger.info(f"[{worker_id}] Waiting for LSP and Joern setup to complete...")
        wait_and_set_states(timeout, poll_interval)
        logger.info(f"[{worker_id}] LSP and Joern setup is ready!")
    else:
        # This is the main process
        crs_multilang_path = Path(config.getoption("--crs-multilang-path"))
        targets = [
            "aixcc/c/babynginx",
            "aixcc/jvm/jenkins",
            "aixcc/c/mock-c",
            "aixcc/jvm/mock-java",
        ]
        setup_context["lsp"] = _setup_lsp(crs_multilang_path, targets)
        setup_context["joern"] = _setup_joern(crs_multilang_path, targets)

        def run_loop():
            global setup_loop, setup_context
            setup_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(setup_loop)
            lsp_states = setup_loop.run_until_complete(
                setup_context["lsp"].__aenter__()
            )
            logger.info("✅ LSP servers setup completed")
            joern_states = setup_loop.run_until_complete(
                setup_context["joern"].__aenter__()
            )
            logger.info("✅ Joern servers setup completed")

            redis_host = _redis_host()
            # Use db=1 for testing
            client = init_redis_with_retry(redis_host=redis_host, db=1)
            client.flushdb()

            setup_ready_flag.write_text("ready")
            lsp_states_file.write_text(json.dumps(lsp_states))
            joern_states_file.write_text(json.dumps(joern_states))
            setup_loop.run_forever()

        if setup_ready_flag.exists():
            setup_ready_flag.unlink()
        if lsp_states_file.exists():
            lsp_states_file.unlink()
        if joern_states_file.exists():
            joern_states_file.unlink()

        setup_thread = threading.Thread(
            target=run_loop, name="pytest-setup-thread", daemon=True
        )
        setup_thread.start()

        wait_and_set_states(timeout, poll_interval)
        logger.info("✅ LSP and Joern setup is ready!")

        atexit.register(_cleanup_config)


def _cleanup_config():
    global setup_context, setup_loop, setup_thread
    logger.info(f"setup_loop: {setup_loop}")
    logger.info(f"setup_context: {setup_context}")
    logger.info(f"setup_thread: {setup_thread}")
    if setup_loop and setup_context and setup_thread:
        logger.info("🧹 Cleaning up LSP and Joern servers...")
        # This is the main process
        lsp_task = asyncio.run_coroutine_threadsafe(
            setup_context["lsp"].__aexit__(None, None, None), setup_loop
        )
        logger.info("✅ LSP servers cleanup completed")
        joern_task = asyncio.run_coroutine_threadsafe(
            setup_context["joern"].__aexit__(None, None, None), setup_loop
        )
        lsp_task.result(timeout=60 * 5)
        joern_task.result(timeout=60 * 5)
        logger.info("✅ Joern servers cleanup completed")
        setup_loop.call_soon_threadsafe(setup_loop.stop)
        setup_thread.join(timeout=10)
        setup_loop.close()
        if setup_ready_flag.exists():
            setup_ready_flag.unlink()
        if lsp_states_file.exists():
            lsp_states_file.unlink()
        if joern_states_file.exists():
            joern_states_file.unlink()
