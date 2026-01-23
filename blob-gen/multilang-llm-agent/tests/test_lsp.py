import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger

from mlla.utils.cp import sCP, sCP_Harness

from .dummy_context import DummyContext

pytest.skip("skipping lsp tests", allow_module_level=True)


@pytest.mark.asyncio
async def test_stress_test_lsp(crs_multilang_path):
    lsp_container_url = "172.17.0.1:3303"
    os.environ["LSP_SERVER_URL"] = lsp_container_url

    threads = []

    for idx in range(100):
        thread = threading.Thread(
            target=lambda: asyncio.run(_test_lsp(idx, crs_multilang_path))
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()


async def _test_lsp(idx, crs_multilang_path):
    cp_name = "aixcc/jvm/r3-apache-commons-compress"
    harness_name = "CompressZipFuzzer"
    workdir = "workdir"
    src_path = (
        crs_multilang_path
        / "benchmarks/projects/aixcc/jvm/r3-apache-commons-compress/repo"
    ).resolve()
    # await asyncio.sleep(idx / 5)

    gc = DummyContext(language="jvm")
    gc.cp = MagicMock(spec=sCP)
    gc.cp.language = "jvm"
    gc.cp.cp_src_path = src_path
    gc.workdir = Path(workdir)
    gc.cp.name = cp_name
    gc.cur_harness = MagicMock(spec=sCP_Harness)
    gc.cur_harness.name = harness_name

    await gc._init_lsp()
    lsp = gc.lsp_server

    relative_file_path = (
        "src/main/java/org/apache/commons/compress/utils/"
        + "FixedLengthBlockOutputStream.java"
    )
    logger.info(f"[{idx}] LSP server started")

    async def _test_lsp_hover(_idx, file_path):
        result = await lsp.request_hover(file_path, 233, 18)
        logger.info(f"[{_idx}] result: {result}")

    async def _test_lsp_definition(_idx, file_path):
        result = await lsp.request_definition(file_path, 233, 18)
        logger.info(f"[{_idx}] result: {result}")

    tasks = []

    for _idx in range(1):
        task = asyncio.create_task(_test_lsp_hover(idx, relative_file_path))
        task2 = asyncio.create_task(_test_lsp_definition(idx, relative_file_path))
        tasks.append(task)
        tasks.append(task2)

    for task in tasks:
        await task


@pytest.mark.asyncio
async def test_stress_test_lsp2(crs_multilang_path):
    lsp_container_url = "172.17.0.1:3307"
    os.environ["LSP_SERVER_URL"] = lsp_container_url

    threads = []

    for idx in range(50):
        thread = threading.Thread(
            target=lambda: asyncio.run(_test_lsp2(idx, crs_multilang_path))
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()


async def _test_lsp2(idx, crs_multilang_path):
    cp_name = "aixcc/jvm/r3-zookeeper"
    harness_name = "MultiProcessTxnFuzzer"
    workdir = "workdir"
    src_path = (
        crs_multilang_path / "benchmarks/projects/aixcc/jvm/r3-zookeeper/repo"
    ).resolve()
    await asyncio.sleep(idx / 10)

    gc = DummyContext(language="jvm")
    gc.cp = MagicMock(spec=sCP)
    gc.cp.language = "jvm"
    gc.cp.cp_src_path = src_path
    gc.workdir = Path(workdir)
    gc.cp.name = cp_name
    gc.cur_harness = MagicMock(spec=sCP_Harness)
    gc.cur_harness.name = harness_name

    await gc._init_lsp()
    lsp = gc.lsp_server

    relative_file_path = (
        "zookeeper-server/src/main/java/org/apache/zookeeper/ClientCnxnSocket.java"
    )
    logger.info(f"[{idx}] LSP server started")

    async def _test_lsp_hover(_idx, file_path):
        result = await lsp.request_hover(file_path, 233, 40)
        logger.info(f"[{_idx}] result: {result}")

    async def _test_lsp_definition(_idx, file_path):
        result = await lsp.request_definition(file_path, 233, 40)
        logger.info(f"[{_idx}] result: {result}")

    tasks = []

    for _idx in range(1):
        task = asyncio.create_task(_test_lsp_hover(idx, relative_file_path))
        task2 = asyncio.create_task(_test_lsp_definition(idx, relative_file_path))
        tasks.append(task)
        tasks.append(task2)

    for task in tasks:
        await task
