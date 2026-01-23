import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from loguru import logger
from redis import Redis

from mlla.agents.cgpa import CGParserAgent, CGParserInputState
from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.utils.bit import LocationInfo
from mlla.utils.cg import FuncInfo
from mlla.utils.cp import sCP
from mlla.utils.llm import LLM
from mlla.utils.redis_utils import init_redis_with_retry
from tests.conftest import RedisInfo
from tests.dummy_context import DummyContext

# pytestmark = pytest.mark.skip(reason="This test is too slow.")


@pytest.mark.skip(reason="This test is too slow.")
@pytest.mark.parametrize("setup_lsp", [["aixcc/jvm/jenkins"]], indirect=True)
@pytest.mark.asyncio
async def test_JenkinsThree(
    setup_lsp: dict, redis_host: str, cp_jenkins_path: Path, jenkins_cp: sCP
):
    cgpa = CGParserAgent(
        DummyContext(
            no_llm=False, language="java", redis_host=redis_host, scp=jenkins_cp
        )
    )
    fn_name = "getResult"
    fn_path = cp_jenkins_path.resolve() / (
        "repo/plugins/code-coverage-api-plugin/src/main/java/io/jenkins/plugins"
        + "/coverage/CoverageProcessor.java"
    )
    graph = cgpa.compile()
    lsp_container_url = setup_lsp["aixcc/jvm/jenkins"]
    assert lsp_container_url is not None
    os.environ["LSP_SERVER_URL"] = lsp_container_url
    async with cgpa.gc.init():
        res = await graph.ainvoke(
            CGParserInputState(fn_name=fn_name, caller_file_path=fn_path)
        )
        logger.info(res)
        fn_name = "CompatibleObjectInputStream"
        res = await graph.ainvoke(
            CGParserInputState(fn_name=fn_name, caller_file_path=fn_path)
        )
        logger.info(res)


@pytest.mark.skip(reason="This test is too slow.")
@pytest.mark.parametrize("setup_lsp", [["aixcc/c/babynginx"]], indirect=True)
@pytest.mark.asyncio
async def test_babynginx(
    setup_lsp: dict, redis_host: str, cp_babynginx_path: Path, babynginx_cp: sCP
):
    cgpa = CGParserAgent(
        DummyContext(
            no_llm=False, language="c", redis_host=redis_host, scp=babynginx_cp
        )
    )
    lsp_container_url = setup_lsp["aixcc/c/babynginx"]
    assert lsp_container_url is not None
    os.environ["LSP_SERVER_URL"] = lsp_container_url
    graph = cgpa.compile()
    async with cgpa.gc.init():
        chals = [
            (
                "ngx_http_finalize_request",
                cp_babynginx_path.resolve() / "repo/src/http/ngx_http_request.c",
            ),
            (
                "ngx_http_init_connection",
                cp_babynginx_path.resolve() / "fuzz/http_request_fuzzer.cc",
            ),
            (
                "ngx_http_init_connection",
                cp_babynginx_path.resolve() / "repo/src/http/ngx_http_request.c",
            ),
            (
                "ngx_atomic_fetch_add",
                cp_babynginx_path.resolve() / "repo/src/core/ngx_resolver.c",
            ),
            (
                "ngx_unescape_uri",
                cp_babynginx_path.resolve() / "repo/src/http/ngx_http_parse.c",
            ),
            (
                "ngx_stream_compile_complex_value",
                cp_babynginx_path.resolve()
                / "repo/src/stream/ngx_stream_proxy_module.c",
            ),
        ]
        tasks = []
        for i in range(100):
            for fn_name, fn_path in chals[0:1]:
                tasks.append(
                    asyncio.create_task(
                        graph.ainvoke(
                            CGParserInputState(
                                fn_name=fn_name, caller_file_path=fn_path
                            )
                        )
                    )
                )

        res = await asyncio.gather(*tasks)
        for r in res:
            logger.info(r)


@pytest.fixture
def cgpa_input_state():
    return CGParserInputState(
        messages=[],
        fn_name="process_input_header",
        fn_file_path=None,
        caller_file_path=None,
        caller_fn_body=None,
        callsite_location=None,
        callsite_range=None,
    )


@pytest_asyncio.fixture
async def cgpa_mockc_setup(
    redis_container: RedisInfo,
    random_project_name: str,
    cp_mockc_path: Path,
):
    redis_host = redis_container.host
    context = DummyContext(no_llm=False, language="c", redis_host=redis_host)
    cgpa = CGParserAgent(context)

    redis_client = init_redis_with_retry(redis_host)
    cgpa.gc.code_indexer = CodeIndexer(redis_client)
    await cgpa.gc.code_indexer.index_project(
        random_project_name, [cp_mockc_path], "c", overwrite=True
    )
    return cgpa


@pytest_asyncio.fixture
async def cgpa_mockc_no_llm(
    redis_container: RedisInfo,
    random_project_name: str,
    cp_mockc_path: Path,
):
    redis_host = redis_container.host
    context = DummyContext(no_llm=False, language="c", redis_host=redis_host)
    cgpa = CGParserAgent(context, no_llm=True)

    redis_client = init_redis_with_retry(redis_host)
    cgpa.gc.code_indexer = CodeIndexer(redis_client)
    await cgpa.gc.code_indexer.index_project(
        random_project_name, [cp_mockc_path], "c", overwrite=True
    )
    return cgpa


@pytest.mark.asyncio
async def test_cgpa_preprocess_miss(
    cgpa_mockc_setup,
    cgpa_input_state: CGParserInputState,
):
    """A case of the first CGPA attempt"""
    cgpa = cgpa_mockc_setup
    cgpa.gc.redis.delete("cgpa::process_input_header")
    graph = cgpa.compile()

    state = cgpa_input_state.copy()
    nodes = []
    async for res in graph.astream(cgpa_input_state):
        node = list(res.keys())[0]
        update = res[node]
        if update:
            state.update(update)
        nodes.append(node)

    assert "preprocess" in nodes
    assert "get_code_dict_from_fn" in nodes
    assert "finalize" in nodes

    code_dict = state.get("code_dict")
    assert code_dict is not None
    assert "process_input_header" in code_dict.func_location.func_name
    assert "mock.c" in code_dict.func_location.file_path


@pytest.fixture
def target_1_code_dict():
    return FuncInfo(
        func_location=LocationInfo(
            func_name="process_input_header",
            file_path="/path/to/mock.c",
            start_line=8,
            end_line=12,
        ),
        func_body=(
            "void process_input_header(const uint8_t *data, size_t size) {\n  char"
            " buf[0x40];\n  if (size > 0 && data[0] == 'A')\n      memcpy(buf, data,"
            " size);\n}"
        ),
        children=[],
        need_to_analyze=False,
        tainted_args=[],
        sink_detector_report=None,
        interest_info=None,
    )


@pytest.mark.asyncio
async def test_cgpa_preprocess_hit(
    cgpa_mockc_setup,
    cgpa_input_state: CGParserInputState,
    target_1_code_dict: FuncInfo,
):
    """A case that previous CGPA found the code"""
    cgpa = cgpa_mockc_setup
    cgpa.gc.redis.delete("cgpa::process_input_header")
    cgpa.gc.redis.set(
        "cgpa::process_input_header", target_1_code_dict.model_dump_json()
    )
    graph = cgpa.compile()

    state = cgpa_input_state.copy()
    nodes = []
    async for res in graph.astream(cgpa_input_state):
        node = list(res.keys())[0]
        update = res[node]
        if update:
            state.update(update)
        nodes.append(node)

    assert "preprocess" in nodes
    assert "get_code_dict_from_fn" not in nodes
    assert "finalize" in nodes

    code_dict = state.get("code_dict")
    assert code_dict is not None
    assert "process_input_header" in code_dict.func_location.func_name
    assert "mock.c" in code_dict.func_location.file_path


@pytest.mark.asyncio
async def test_cgpa_preprocess_hit_invalid(
    cgpa_mockc_setup,
    cgpa_input_state: CGParserInputState,
):
    """A case that previous CGPA failed to find the code"""
    cgpa = cgpa_mockc_setup
    cgpa.gc.redis.delete("cgpa::dummy::process_input_header")
    cgpa.gc.redis.set("cgpa::dummy::process_input_header", "None")
    graph = cgpa.compile()

    state = cgpa_input_state.copy()
    nodes = []
    async for res in graph.astream(cgpa_input_state):
        node = list(res.keys())[0]
        update = res[node]
        if update:
            state.update(update)
        nodes.append(node)

    assert "preprocess" in nodes
    assert "get_code_dict_from_fn" not in nodes, "Do not repeat the search"
    assert "finalize" in nodes

    code_dict = state.get("code_dict")
    assert code_dict is None, "No code dict"


@pytest.mark.asyncio
async def test_cgpa_no_llm(
    monkeypatch,
    cgpa_mockc_no_llm,
    cgpa_input_state: CGParserInputState,
):
    async_spy = AsyncMock()
    monkeypatch.setattr(LLM, "ainvoke", async_spy)
    sync_spy = Mock()
    monkeypatch.setattr(LLM, "invoke", sync_spy)

    cgpa = cgpa_mockc_no_llm
    graph = cgpa.compile()

    # Searched by CGPA tools
    state = cgpa_input_state.copy()
    tag = "cgpa::dummy::process_input_header"
    cgpa.gc.redis.delete(tag)
    result = await graph.ainvoke(state)
    assert "code_dict" in result
    assert result["code_dict"] is not None
    assert async_spy.call_count == 0 and sync_spy.call_count == 0

    # Supposed not to search by LLM by no_llm=True
    invalid_state = cgpa_input_state.copy()
    invalid_state["fn_name"] = "invalid_fn"
    cgpa.gc.redis.delete("cgpa::dummy::invalid_fn")
    result = await graph.ainvoke(invalid_state)
    assert "code_dict" in result
    assert result["code_dict"] is None
    assert async_spy.call_count == 0 and sync_spy.call_count == 0


@pytest.mark.asyncio
# @pytest.mark.xfail(reason="LLM is not used now due to the cost.")
async def test_cgpa_llm(
    monkeypatch,
    cgpa_mockc_setup,
    cgpa_input_state: CGParserInputState,
):
    async_spy = AsyncMock()
    monkeypatch.setattr(LLM, "ainvoke", async_spy)
    sync_spy = Mock()
    monkeypatch.setattr(LLM, "invoke", sync_spy)

    cgpa = cgpa_mockc_setup
    graph = cgpa.compile()

    # Supposed to search by LLM by no_llm=False
    invalid_state = cgpa_input_state.copy()
    invalid_state["fn_name"] = "invalid_fn"
    cgpa.gc.redis.delete("cgpa::invalid_fn")
    result = await graph.ainvoke(invalid_state)
    assert "code_dict" in result
    assert result["code_dict"] is None
    assert (
        async_spy.call_count == 0 and sync_spy.call_count == 0
    ), "Current CGPA does not use LLM even if no_llm=False"


@pytest.mark.skip(reason="This test is too slow.")
@pytest.mark.parametrize("setup_lsp", [["aixcc/c/r3-sqlite3"]], indirect=True)
@pytest.mark.asyncio
async def test_r3_sqlite3(
    setup_lsp: dict, redis_host: str, cp_r3_sqlite3_path: Path, redis_client: Redis
):
    import sys

    cp, scp = sCP.from_cp_path(cp_r3_sqlite3_path, "customfuzz3")

    cgpa_keys: Any = redis_client.keys(f"cgpa::{cp.name}::*")
    for key in cgpa_keys:
        redis_client.delete(key)

    mcga_keys: Any = redis_client.keys(f"mcga::{cp.name}::{scp.name}::*")
    for key in mcga_keys:
        redis_client.delete(key)

    logger.add(sys.stderr, level="DEBUG")
    mock_config = DummyContext(
        no_llm=False, language="c", redis_host=redis_host, scp=scp
    )
    mock_config._cp = cp
    # mock_config.update_tracer_result = AsyncMock()

    cgpa = CGParserAgent(config=mock_config)
    fn_name = "shell_main"
    fn_path = "shell.c"
    graph = cgpa.compile()
    lsp_container_url = setup_lsp["aixcc/c/r3-sqlite3"]
    assert lsp_container_url is not None
    os.environ["LSP_SERVER_URL"] = lsp_container_url
    caller_file_path = cp_r3_sqlite3_path.resolve() / "repo/test/customfuzz3.c"
    logger.info(f"Caller file path: {caller_file_path}")
    async with cgpa.gc.init():
        res = await graph.ainvoke(
            CGParserInputState(
                fn_name=fn_name,
                fn_file_path=fn_path,
                caller_file_path=caller_file_path,
                callsite_location=(27, 3),
            )
        )
        logger.info(res)
