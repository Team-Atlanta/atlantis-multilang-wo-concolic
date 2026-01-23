#!/usr/bin/env python3

import asyncio
from libCRS.challenge import init_cp_in_runner, CP
from loguru import logger
from multilspy.multilspy_config import MultilspyConfig
from multilspy.language_server import LanguageServer
from multilspy.multilspy_logger import MultilspyLogger
import os
from pathlib import Path

def check_compile_commands(src_path: Path):
    compile_commands_json = src_path / "compile_commands.json"
    if not compile_commands_json.exists():
        logger.error(f"Compile commands json file not found: {compile_commands_json}")
        exit(-1)
    else:
        if compile_commands_json.stat().st_size == 0:
            logger.error(f"Compile commands json file is empty: {compile_commands_json}")
            exit(-1)
    

async def main():
    cp: CP = init_cp_in_runner()

    if cp.language == "c" or cp.language == "c++":
        language = "c"
        check_compile_commands(cp.cp_src_path)
    elif cp.language == "jvm":
        language = "java"
    else:
        language = cp.language

    logger.info(f"Sending request to LSP server for {cp.name}")
    config = MultilspyConfig.from_dict(
        {
            "code_language": language,
            # "trace_lsp_communication": True,
        }
    )

    src_path = cp.cp_src_path.resolve().as_posix()
    msp_logger = MultilspyLogger()
    lsp: LanguageServer = LanguageServer.create(config, msp_logger, src_path)

    harness = list(cp.harnesses.values())[0]
    harness_src_path = harness.src_path

    # Get relative path of harness_src_path from src_path
    harness_src_path = os.path.relpath(harness_src_path, src_path)

    # send request to LSP server
    async with lsp.start_server():
        logger.info("LSP server started")
        logger.info(f"Requesting document symbols for {harness_src_path}")
        # wait for response
        # timeout for 15 mins
        try:
            symbols, tree = await asyncio.wait_for(lsp.request_document_symbols(harness_src_path), timeout=900)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for response from LSP server")
            exit(-1)
        if len(symbols) == 0:
            logger.error("No symbols found")
            exit(-1)
        logger.info(f"Symbols: {symbols}")



if __name__ == "__main__":
    asyncio.run(main())
