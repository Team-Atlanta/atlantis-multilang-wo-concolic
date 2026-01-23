import asyncio
import os
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger

from .setup_docker import SetupDocker


class SetupLSPDocker(SetupDocker):
    def __init__(self, crs_multilang_path: Path, cp_name: str):
        super().__init__(crs_multilang_path, cp_name)

    @asynccontextmanager
    async def setup(self, port: Optional[int] = None) -> AsyncGenerator[str, None]:
        safe_cp_name = self.cp_name.replace("/", "-")
        lsp_proj_name = f"ci-{safe_cp_name}-{uuid.uuid4()}"

        rand_port = 33033 + random.randint(0, 1000)

        # If port is not specified, find an available one
        if port is None:
            port = self._find_available_port(rand_port)

        # Start LSP server
        child_env = os.environ.copy()
        child_env["PROJ_NAME"] = lsp_proj_name
        child_env["PORT"] = str(port)
        proc = await asyncio.create_subprocess_shell(
            f"./bin/run_lsp_server {self.crs_multilang_path} {self.cp_name}",
            env=child_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        logger.info(f"LSP server started with project name: {lsp_proj_name}")

        # check the lsp_proj_name is running in docker ps
        while True:
            lsp_container_name = self.is_container_running(lsp_proj_name)
            if lsp_container_name is not None:
                logger.info(f"LSP server is running with project name: {lsp_proj_name}")
                break
            await asyncio.sleep(1)

        # Return lsp_container_name
        try:
            yield lsp_container_name
        finally:
            # Stop LSP server
            logger.info(f"Stopping LSP server with project name: {lsp_proj_name}")
            proc = await asyncio.create_subprocess_shell(
                f"./bin/down_lsp_server {self.crs_multilang_path} {self.cp_name}",
                env=child_env,
            )
            await proc.wait()
