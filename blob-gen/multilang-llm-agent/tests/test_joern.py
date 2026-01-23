import asyncio
import os
import threading
import time

import pytest
from loguru import logger
from mljoern.client import JoernClient

from mlla.utils.joern_adaptor import check_joern, restart_joern


@pytest.mark.parametrize("setup_joern", [["aixcc/c/mock-c"]], indirect=True)
def test_joern_until_fail(setup_joern):
    os.environ["JOERN_URL"] = setup_joern["aixcc/c/mock-c"]
    logger.info(f"JOERN_URL: {os.environ['JOERN_URL']}")

    joern_client = JoernClient()
    while True:
        try:
            if joern_client._check_joern():
                logger.info("Joern server is ready")
                break
        except Exception:
            pass
        time.sleep(1)

    res = joern_client.check_health()
    logger.info(f"Check health result: {res}")
    assert joern_client.query("select 1") is not None
    assert joern_client.query("select 2") is not None
    res = joern_client.restart()
    logger.info(f"Restart result: {res}")
    res = joern_client.check_health()
    logger.info(f"Check health result: {res}")
    assert joern_client.query("select 1") is not None
    assert joern_client.query("select 2") is not None


@pytest.mark.skip(reason="skipping local tests")
@pytest.mark.asyncio
async def test_stress_test_joern():

    joern_url = "172.17.0.1:9926"
    logger.info(f"JOERN_URL: {joern_url}")
    os.environ["JOERN_URL"] = joern_url

    threads = []

    for idx in range(10):
        thread = threading.Thread(target=lambda: asyncio.run(_test_joern_thread1(idx)))
        thread2 = threading.Thread(target=lambda: asyncio.run(_test_joern_thread2(idx)))
        thread.start()
        thread2.start()
        threads.append(thread)
        threads.append(thread2)

    # Wait for all threads to complete
    for thread in threads:
        thread.join()


async def _test_joern_thread1(idx):
    lock = asyncio.Lock()
    await _test_joern(idx, lock)


async def _test_joern_thread2(idx):
    lock = asyncio.Lock()
    await _test_joern2(idx, lock)


async def _test_joern(idx, lock):
    joern_client = JoernClient()
    await check_joern(joern_client, lock)
    if idx == 0:
        await restart_joern(joern_client, "cpg.method.size", lock, True)
    else:
        await restart_joern(joern_client, "cpg.method.size", lock)

    await check_joern(joern_client, lock)
    logger.info(f"[{idx}] Check health result: {joern_client.check_health()}")


async def _test_joern2(idx, lock):
    joern_client = JoernClient()
    await check_joern(joern_client, lock)
    res = joern_client.query("cpg.method.size")
    logger.info(f"[{idx}] Query 1 result: {res}")
    assert res is not None
    res = joern_client.query("cpg.call.size")
    logger.info(f"[{idx}] Query 2 result: {res}")
    assert res is not None
