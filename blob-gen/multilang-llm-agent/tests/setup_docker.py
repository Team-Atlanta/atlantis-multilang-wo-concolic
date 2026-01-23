import os
import subprocess
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

# from loguru import logger


class SetupDocker(ABC):
    def __init__(self, crs_multilang_path: Path, cp_name: str):
        crs_multilang = os.getenv("CRS_MULTILANG")
        if crs_multilang is None:
            self.crs_multilang_path = crs_multilang_path.resolve()
        else:
            self.crs_multilang_path = Path(crs_multilang)

        self.cp_name = cp_name

    @abstractmethod
    @asynccontextmanager
    def setup(self, port: Optional[int] = None) -> AsyncIterator[str]:
        pass

    def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is already in use by Docker."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Ports}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            # Check if port is in any of the port mappings
            for line in result.stdout.splitlines():
                if f"172.17.0.1:{port}->" in line:
                    return True
            return False
        except subprocess.CalledProcessError:
            return True  # If we can't check, assume port is in use

    def _find_available_port(self, start_port: int) -> int:
        """Find an available port starting from start_port."""
        port = start_port
        while self._is_port_in_use(port):
            port += 1
        return port

    def is_container_running(self, container_name_prefix: str) -> Optional[str]:
        """Check if a Docker container with the given name is running and return
        its port forwarding info.

        Args:
            container_name_prefix: Prefix of the Docker container name to check

        Returns:
            Optional[str]: Port forwarding info (e.g., "172.17.0.1:3303") if
            container is running, None otherwise
        """
        try:
            # Get container ID first using name prefix
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}} {{.Names}}"],
                capture_output=True,
                text=True,
                check=True,
            )

            # Find container ID by matching name prefix
            container_id = None
            for line in result.stdout.splitlines():
                cid, name = line.strip().split(" ", 1)
                if name.startswith(container_name_prefix):
                    container_id = cid
                    break

            if not container_id:
                return None

            # Get port mapping info
            result = subprocess.run(
                ["docker", "port", container_id],
                capture_output=True,
                text=True,
                check=True,
            )

            stdout = result.stdout
            # logger.info(f"Port mapping info: {stdout}")

            # Parse port mapping info
            for line in stdout.splitlines():
                if "->" in line:
                    # Extract the host part (e.g., "172.17.0.1:3303")
                    host_part = line.split("->")[1].strip()
                    return host_part

            return None
        except subprocess.CalledProcessError:
            return None
