import asyncio
import os
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger

from .setup_docker import SetupDocker


class SetupJoernDocker(SetupDocker):
    def __init__(self, crs_multilang_path: Path, cp_name: str):
        super().__init__(crs_multilang_path, cp_name)

    @asynccontextmanager
    async def setup(self, port: Optional[int] = None) -> AsyncGenerator[str, None]:
        safe_cp_name = self.cp_name.replace("/", "-")
        joern_proj_name = f"ci-{safe_cp_name}-{uuid.uuid4()}"

        rand_port = 59099 + random.randint(0, 1000)

        # If port is not specified, find an available one
        if port is None:
            port = self._find_available_port(rand_port)

        # Start Joern server
        child_env = os.environ.copy()
        child_env["PROJ_NAME"] = joern_proj_name
        child_env["PORT"] = str(port)
        proc = await asyncio.create_subprocess_shell(
            f"./bin/run_joern_server {self.crs_multilang_path} {self.cp_name}",
            env=child_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        logger.info(f"Joern server started with project name: {joern_proj_name}")

        # check the lsp_proj_name is running in docker ps
        while True:
            lsp_container_name = self.is_container_running(joern_proj_name)
            if lsp_container_name is not None:
                logger.info(
                    f"Joern server is running with project name: {joern_proj_name}"
                )
                break
            await asyncio.sleep(1)

        # Return lsp_container_name
        try:
            yield lsp_container_name
        finally:
            # Stop Joern server
            logger.info(f"Stopping Joern server with project name: {joern_proj_name}")
            proc = await asyncio.create_subprocess_shell(
                f"./bin/down_joern_server {self.crs_multilang_path} {self.cp_name}",
                env=child_env,
            )
            await proc.wait()
