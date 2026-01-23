import asyncio
from typing import Any

import orjson
from loguru import logger
from mljoern.client import JoernClient


def fix_query(query_str):
    """Utility method to convert CPGQL queries to become json friendly"""
    if "\\." in query_str and "\\\\." not in query_str:
        query_str = query_str.replace("\\.", "\\\\.")
    if (query_str.startswith("cpg.") or query_str.startswith("({cpg.")) and (
        not query_str.endswith(".toJson")
        and not query_str.endswith(".plotDot")
        and not query_str.endswith(".p")
        and not query_str.endswith(".store")
        and not query_str.endswith(".printCallTree")
    ):
        query_str = f"{query_str}.toJsonPretty"
    return query_str


def fix_json(sout: str) -> Any:
    """Hacky method to convert the joern stdout string to json"""
    source_sink_mode = False
    original_sout = sout
    try:
        if "defined function source" in sout:
            source_sink_mode = True
            sout = sout.replace("defined function source\n", "")
            sout = sout.replace("defined function sink\n", "")
        else:
            sout = sout.replace(r'"\"', '"').replace(r'\""', '"')
        if ': String = "[' in sout or ": String = [" in sout or source_sink_mode:
            if ": String = [" in sout:
                sout = sout.split(": String = ")[-1]
            elif source_sink_mode:
                sout = (
                    sout.replace(r"\"", '"')
                    .replace('}]}]"', "}]}]")
                    .replace('\\"', '"')
                )
                if ': String = "[' in sout:
                    sout = sout.split(': String = "')[-1]
            else:
                sout = sout.split(': String = "')[-1][-1]
        elif "tree: ListBuffer" in sout or " = ListBuffer(" in sout:
            sout = sout.split(": String = ")[-1]
            if '"""' in sout:
                sout = sout.replace('"""', "")
            return sout
        elif 'String = """[' in sout:
            tmpA = sout.split("\n")[1:-1]
            sout = "[ " + "\n".join(tmpA) + "]"
        sout = sout.replace('"}]"', '"}]')
        return orjson.loads(sout)
    except orjson.JSONDecodeError as e:
        return {"response": original_sout, "error": str(e), "sout": sout}


async def restart_joern(
    joern_client: JoernClient, query: str, joern_lock: asyncio.Lock, force: bool = False
) -> Any:
    try:
        await asyncio.wait_for(joern_lock.acquire(), timeout=20)
        try:
            if joern_client._check_joern():
                res, not_timeout = joern_client.query(query, timeout=10)
                if not_timeout:
                    return res
            logger.warning(f"Joern query {query} failed. Restarting Joern...")
            d = joern_client.restart(force)
            logger.info(f"Restart result: {d}")
            res, not_timeout = joern_client.query(query, timeout=10)
            if not not_timeout:
                return None
            if res.get("success", False) is False:
                return None
            logger.info(f"Joern query {query} succeeded.")
            logger.info(f"res: {res}")

            return res
        finally:
            joern_lock.release()
    except asyncio.TimeoutError:
        logger.error("Restarting Joern failed. Returning None.")
        return None


async def query_joern(
    joern_client: JoernClient, query: str, joern_lock: asyncio.Lock
) -> Any:
    """Query Joern and return the results"""
    query = fix_query(query)
    retry_count = 0
    while retry_count < 3:
        res, not_timeout = joern_client.query(query, timeout=10)
        if not_timeout:
            break
        retry_count += 1
        logger.warning(f"Joern query timed out: {query}")

    if not res or res.get("success", False) is False:
        res = await restart_joern(joern_client, query, joern_lock)
        if not res:
            return None

    retry_count = 0
    while retry_count < 3:
        try:
            stdout = res.get("stdout")
            if not stdout:
                return None

            rjson = fix_json(stdout)
            if not rjson:
                return None
            elif isinstance(rjson, dict) and rjson.get("error") and rjson.get("sout"):
                res = await restart_joern(joern_client, query, joern_lock)
            else:
                return rjson
        finally:
            retry_count += 1

    return None


async def check_joern(joern_client: JoernClient, joern_lock: asyncio.Lock) -> bool:
    restart_count = 0
    retry_limit = 15
    while restart_count < 3:
        retry_count = 0
        while retry_count < retry_limit:
            try:
                if joern_client._check_joern():
                    logger.info("Joern server is ready")
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
            retry_count += 1
        if retry_count == retry_limit:
            success = await restart_joern(joern_client, "cpg.method.size", joern_lock)
            if success:
                return True
            else:
                restart_count += 1
                retry_limit *= 2
                continue

    if restart_count == 3:
        logger.error("Joern server is not ready")
        return False
    return True
