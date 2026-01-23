import atexit
from contextlib import asynccontextmanager
import os
import random
import socket
import subprocess
import time

import psutil
from loguru import logger
from libCRS import init_cp_in_runner
from client import JoernClient
import asyncio
from fastapi import FastAPI, Request
import uvicorn

PORT = 9909


def exec_run(cmd: str, stdout: bool = True, stderr: bool = True):
    p_stdout = None
    p_stderr = None
    if not stdout:
        p_stdout = subprocess.DEVNULL
    if not stderr:
        p_stderr = subprocess.DEVNULL

    result = subprocess.run(cmd, shell=True, stdout=p_stdout, stderr=p_stderr)

    return result.returncode


class JoernServer:
    def __init__(self, memory=12, stop_on_exit=False):
        self.__port = PORT
        self.__server = None
        self.__server_stdout_reader: asyncio.StreamReader = None
        self.__server_stdin_writer: asyncio.StreamWriter = None
        self.__memory = memory
        self.cp = init_cp_in_runner()
        self.__cpg_path = self.create_cpg()
        self.__restart_lock = asyncio.Lock()
        self.__query_lock = asyncio.Lock()

        if stop_on_exit:
            atexit.register(self.stop)

    @property
    def cpg_path(self):
        return self.__cpg_path

    @property
    def port(self):
        return self.__port

    def stop(self):
        logger.warning(" - Joern server stop...")
        try:
            parent = psutil.Process(self.__server.pid)
        except Exception:
            logger.warning(" - Joern server not running")
            self.__server = None
            return
        try:
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            _, still_alive = psutil.wait_procs(children, timeout=3)
            for p in still_alive:
                p.kill()
            self.__server.terminate()
            self.__server.wait(3)
        except Exception:
            logger.warning(" - Joern server kill...")
            self.__server.kill()
        self.__server = None

    async def restart(self, force: bool = False):
        try:
            await asyncio.wait_for(self.__restart_lock.acquire(), timeout=60 * 5)
            try:
                msg, valid = await self.check_health()
                logger.warning(f"Joern server health check: {msg}")
                if not force and valid:
                    return
                self.stop()
                await self.start()
            finally:
                self.__restart_lock.release()
        except asyncio.TimeoutError:
            raise Exception("Restart timeout")


    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("localhost", port)) != 0:
                return False
            else:
                return True

    def _check_joern_running(self) -> bool:
        processes = psutil.process_iter(["cmdline"])
        for process in processes:
            if process.info["cmdline"] is None:
                continue

            cmdline = " ".join(process.info["cmdline"])

            if "joern" in cmdline and str(self.cpg_path) in cmdline:
                logger.info(f"Joern process found: {process.info}")
                return True

        logger.warning("No joern process found")
        return False

    async def check_health(self) -> tuple[str, bool]:
        res = self._check_joern_running()
        if not res:
            return "No joern process found", False

        try:
            res = await asyncio.wait_for(self.query("cpg.method.size"), timeout=10)
            size_str = res.split("= ")[-1].split("\n")[0].strip()
            method_size = int(size_str)

            return "Success", method_size >= 1
        except Exception as e:
            logger.warning(f"Joern server check health error: {e}")
            return str(e), False

    @staticmethod
    def _find_all_joern_processes():
        processes = psutil.process_iter(["cmdline"])
        for process in processes:
            if process.info["cmdline"] is None:
                continue

            cmdline = " ".join(process.info["cmdline"])

            if "joern --server --server-port" in cmdline:
                yield process

    # ! Should be dangerous??
    @staticmethod
    def _kill_all_joern_servers():
        try:

            def _kill_proc(process: psutil.Process):
                process.terminate()
                process.wait(timeout=3)
                if process.is_running():
                    process.kill()

            all_joern_processes = list(JoernServer._find_all_joern_processes())

            for process in all_joern_processes:
                for child in process.children(recursive=True):
                    _kill_proc(child)
                _kill_proc(process)
        except Exception:
            logger.warning("Failed to kill all joern servers")

    async def start(self):
        async def __run() -> None:
            cmd = [
                f"/joern/joern-cli/joern",
                "--server",
                "--server-host",
                "0.0.0.0",
                "--server-port",
                f"{self.__port}",
                f"{self.__cpg_path}",
                "--nocolors",
            ]
            env = os.environ.copy()
            env["JAVA_OPTS"] = (
                f"-Xmx{self.__memory}G -XX:ParallelGCThreads=8 -XX:ConcGCThreads=4 -Djava.util.concurrent.ForkJoinPool.common.parallelism=20"
            )
            # cmd = [
            #     "socat",
            #     "-d",
            #     "-d",
            #     "-ly",
            #     "-lf",
            #     log_tf.name,
            #     f"TCP-LISTEN:{self.__port},bind=0.0.0.0,reuseaddr,reuseport,forever,fork,keepalive",
            #     f"EXEC:\'{/joern/joern-cli/joern} {self.__cpg_path}\'"
            # ]
            cmd = [
                "/joern/joern-cli/joern",
                self.__cpg_path,
                "--nocolors",
            ]
            logger.debug(f"Joern command: {cmd}")

            self.__server = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=1024 * 1024 * 10,  # 10MB
            )

            self.__server_stdin_writer = self.__server.stdin
            self.__server_stdout_reader = self.__server.stdout
            async with self.__query_lock:
                await self.__server_stdout_reader.readuntil(b"joern>")

        url = f"localhost:{self.__port}"
        os.environ["JOERN_URL"] = url
        await __run()

    async def query(self, query: str) -> str:
        try:
            await asyncio.wait_for(self.__query_lock.acquire(), timeout=10)
            try:
                if self.__server is None or self.__server_stdin_writer is None or self.__server_stdout_reader is None:
                    raise Exception("Joern server is not running")

                if not query.endswith("\n"):
                    query += "\n"
                self.__server_stdin_writer.write(query.encode("utf-8"))
                await self.__server_stdin_writer.drain()
                result = await self.__server_stdout_reader.readuntil(b"joern>")
                return result.decode("utf-8")
            finally:
                self.__query_lock.release()
        except asyncio.TimeoutError:
            raise Exception("Query timeout")

    def create_cpg(self):
        cpg_path = f"/out/joern-cpg/{self.cp.name}.cpg.bin"

        # Check if joern cpg already exists

        result = exec_run(f"test -f {cpg_path}")
        if result == 0:
            logger.debug(f"Database already exists at {cpg_path}, skipping creation")
            return cpg_path

        logger.debug(f"Creating CPG for {self.cp.name}")

        # Check elapsed time
        start_time = time.time()

        cmd = None
        match self.cp.language:
            case "c":
                cmd = " ".join(
                    [
                        f"/joern/joern-cli/c2cpg.sh",
                        "/src",
                        "--exclude=/src/aflplusplus",
                        "--exclude=/src/fuzztest",
                        "--exclude=/src/honggfuzz",
                        "--exclude=/src/libfuzzer",
                        "-J-Xmx12g",
                        "--output=" + cpg_path,
                    ]
                )
            case "cpp" | "c++":
                cmd = " ".join(
                    [
                        f"/joern/joern-cli/c2cpg.sh",
                        "/src",
                        "--exclude=/src/aflplusplus",
                        "--exclude=/src/fuzztest",
                        "--exclude=/src/honggfuzz",
                        "--exclude=/src/libfuzzer",
                        "-J-Xmx12g",
                        "--output=" + cpg_path,
                    ]
                )
            case "jvm":
                cmd = " ".join(
                    [
                        f"/joern/joern-cli/javasrc2cpg",
                        "/src",
                        "--exclude=/src/aflplusplus",
                        "--exclude=/src/fuzztest",
                        "--exclude=/src/honggfuzz",
                        "--exclude=/src/libfuzzer",
                        "-J-Xmx12g",
                        "--output=" + cpg_path,
                    ]
                )

        if cmd is None:
            logger.error(f"Unsupported language: {self.cp.language}")
            return

        logger.debug(f"Command: {cmd}")
        result = exec_run(cmd, stdout=True, stderr=True)

        if result != 0:
            logger.error(f"Command failed with exit code: {result}")

        elapsed_time = time.time() - start_time
        logger.debug(f"CPG created for {self.cp.name} in {elapsed_time} seconds")

        return cpg_path


server = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global server
    server = JoernServer()
    logger.info("Joern server starting...")
    await server.start()
    logger.info("Joern server started")
    yield
    logger.info("Joern server stopping...")
    await server.stop()


app = FastAPI(lifespan=lifespan)
query_sink_lock = asyncio.Lock()


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/check-health")
async def check_health():
    d = {
        "success": False,
        "stdout": "",
        "stderr": "",
    }
    if server is None:
        d["stderr"] = "Joern server is not launched"
        return d
    res, valid = await server.check_health()
    if not valid:
        d["stderr"] = res
        return d
    d["success"] = True
    d["stdout"] = res
    return d


@app.post("/query-sync")
async def query_sync(request: Request):
    async with query_sink_lock:
        data = await request.json()
        query = data.get("query")
        query = query.replace("\n", " ")

        d = {
            "success": False,
            "stdout": "",
            "stderr": "",
        }

        # logger.info(f"Query: {query}")

        try:
            result = await asyncio.wait_for(server.query(query), timeout=10)
            if "\n\n" in result:
                d["success"] = True
                d["stdout"] = result.split("\n\n")[0].strip()
            else:
                d["stderr"] = "Invalid result: " + result
                logger.warning(f"Query: {query}")
                logger.warning(f"Invalid result: {d}")
        except TimeoutError:
            d["stderr"] = "Timeout"
            logger.warning(f"Query: {query}")
            logger.warning(f"Timeout: {d}")
        except Exception as e:
            d["stderr"] = str(e)
            logger.warning(f"Query: {query}")
            logger.warning(f"Query result: {d}")

        # logger.info(f"Query result: {d}")

        return d


@app.get("/restart")
async def restart(request: Request):
    global server
    d = {
        "success": False,
        "stdout": "",
        "stderr": "",
    }
    try:
        logger.warning("Joern server restarting...")
        await server.restart(request.headers.get("force", "false") == "true")
        logger.warning("Joern server restarted")
        d["success"] = True
    except Exception as e:
        d["stderr"] = str(e)
    return d


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
