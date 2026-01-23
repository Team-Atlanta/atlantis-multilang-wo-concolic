import asyncio
import json
import logging
import os
import pathlib
import stat
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from multilspy import multilspy_types
from multilspy.multilspy_logger import MultilspyLogger
from multilspy.language_server import LanguageServer
from multilspy.lsp_protocol_handler.server import ProcessLaunchInfo
from multilspy.lsp_protocol_handler.lsp_types import InitializeParams
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_utils import FileUtils
from multilspy.multilspy_utils import PlatformUtils


class ClangdServer(LanguageServer):
    server_executable_path: str

    def __init__(self, config: MultilspyConfig, logger: MultilspyLogger, repository_root_path: str):
        """
        Creates a ClangdServer instance. This class is not meant to be instantiated directly. Use LanguageServer.create() instead.
        """
        clangd_executable_path = self.setup_runtime_dependencies(logger, config)
        cmd = " ".join(
            [
                clangd_executable_path,
                "--log=verbose",
                "--pretty",
                "-j=32",
                "--completion-style=detailed",
                "--header-insertion=iwyu",
                "--clang-tidy",
                "--all-scopes-completion",
                "-index-file=clangd.dex"
            ]
        )
        super().__init__(
            config,
            logger,
            repository_root_path,
            ProcessLaunchInfo(cmd=cmd, cwd=repository_root_path),
            "c",
        )
        self.service_ready_event = asyncio.Event()
        self.indexing_begin_event = asyncio.Event()
        self.indexing_end_event = asyncio.Event()
        self.server_executable_path = clangd_executable_path
        self.config = config
    
    @staticmethod
    def download_server(logger: MultilspyLogger):
        platform_id = PlatformUtils.get_platform_id()

        with open(os.path.join(os.path.dirname(__file__), "runtime_dependencies.json"), "r") as f:
            d = json.load(f)
            del d["_description"]

        assert platform_id.value in [
            "linux-x64",
        ], "Only linux-x64 platform is supported for in multilspy at the moment"

        runtime_dependencies = d["runtimeDependencies"]
        runtime_dependencies = [
            dependency for dependency in runtime_dependencies if dependency["platformId"] == platform_id.value
        ]

        clangdls_dir = os.path.join(os.path.dirname(__file__), "clangd")

        for dependency in runtime_dependencies:
            exec_path = os.path.join(clangdls_dir, "clangd_20.1.0", "bin", dependency["binaryName"])
            if os.path.exists(exec_path) and os.path.getsize(exec_path) == 0:
                os.remove(exec_path)
            if not os.path.exists(exec_path):
                os.makedirs(clangdls_dir, exist_ok=True)
                if dependency["archiveType"] == "zip":
                    FileUtils.download_and_extract_archive(
                        logger, dependency["url"], clangdls_dir, dependency["archiveType"]
                    )
            assert os.path.exists(exec_path)

            os.chmod(exec_path, stat.S_IEXEC)      
        logger.log("Clangd server downloaded", logging.INFO)

    def setup_runtime_dependencies(self, logger: MultilspyLogger, config: MultilspyConfig) -> str:

        # if not config.is_offline:
        #     self.download_server(logger)

        clangdls_dir = os.path.join(os.path.dirname(__file__), "clangd")
        clangd_executable_path = os.path.join(clangdls_dir, "clangd_20.1.0", "bin", "clangd")

        return clangd_executable_path

    def _get_initialize_params(self, repository_absolute_path: str) -> InitializeParams:
        with open(os.path.join(os.path.dirname(__file__), "initialize_params.json"), "r") as f:
            d = json.load(f)

        del d["_description"]

        d["processId"] = 0
        assert d["rootPath"] == "$rootPath"
        d["rootPath"] = repository_absolute_path

        assert d["rootUri"] == "$rootUri"
        d["rootUri"] = pathlib.Path(repository_absolute_path).as_uri()

        assert d["workspaceFolders"][0]["uri"] == "$uri"
        d["workspaceFolders"][0]["uri"] = pathlib.Path(repository_absolute_path).as_uri()

        assert d["workspaceFolders"][0]["name"] == "$name"
        d["workspaceFolders"][0]["name"] = os.path.basename(repository_absolute_path)

        return d

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["ClangdServer"]:
        """
        Starts the Clangd Language Server, waits for the server to be ready and yields the LanguageServer instance.
        """
        async def register_capability_handler(params):
            assert "registrations" in params
            for registration in params["registrations"]:
                if registration["method"] == "workspace/executeCommand":
                    self.initialize_searcher_command_available.set()
                    self.resolve_main_method_available.set()
            return



        async def execute_client_command_handler(params):
            return []

        async def do_nothing(params):
            self.logger.log(f"LSP: {params}", logging.INFO)
            return



        async def window_log_message(msg):
            self.logger.log(f"LSP: window/logMessage: {msg}", logging.INFO)

        # Allow $/progress notifications to be handled
        async def window_work_done_progress_create_handler(params: dict):
            if not self.config.is_server:
                self.logger.log(f"LSP: [window/workDoneProgress/create] {params}", logging.INFO)

            return None # Acknowledge the request with a null result

        async def progress_handler(params: dict):
            if not self.config.is_server:
                self.logger.log(f"LSP: [$/progress] {params}", logging.INFO)

            token = params.get("token")
            value = params.get("value", {})
            kind = value.get("kind")
            title = value.get("title")

            if token == "backgroundIndexProgress":
                if kind == "begin" and title == "indexing":
                    self.indexing_begin_event.set()
                    self.indexing_end_event.clear()
                elif kind == "report":
                    pass
                elif kind == "end":
                    self.indexing_end_event.set()

        self.server.on_request("client/registerCapability", register_capability_handler)
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_request("workspace/executeClientCommand", execute_client_command_handler)
        self.server.on_notification("$/progress", progress_handler)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)
        self.server.on_notification("language/actionableNotification", do_nothing)
        self.server.on_request("window/workDoneProgress/create", window_work_done_progress_create_handler)
        self.server.on_notification("workspace/semanticTokens/refresh", do_nothing)

        if self.config.is_server:
            self.logger.log("Starting clangd-indexer process", logging.INFO)
            clangd_indexer_path = os.path.dirname(self.server_executable_path) + "/clangd-indexer"
            self.logger.log(f"clangd-indexer path: {clangd_indexer_path}", logging.INFO)
            clangd_cmd = " ".join(
                [
                    clangd_indexer_path,
                    "--executor=all-TUs",
                    "compile_commands.json",
                    ">",
                    "clangd.dex",
                ]
            )
            indexer_process = await asyncio.create_subprocess_shell(
                clangd_cmd,
                cwd=self.repository_root_path,
            )
            res = await indexer_process.wait()
            self.logger.log(f"clangd-indexer process completed with exit code {res}\n cwd: {self.repository_root_path}", logging.INFO)

        self.logger.log("Starting clangd-language-server server process", logging.INFO)
        await self.server.start()
        initialize_params = self._get_initialize_params(self.repository_root_path)

        self.logger.log(
            "Sending initialize request from LSP client to LSP server and awaiting response",
            logging.INFO,
        )
        init_response = await self.server.send.initialize(initialize_params)
        self.logger.log("LSP server sent initialize request", logging.INFO)

        assert init_response["capabilities"]["textDocumentSync"]["change"] == 2
        assert "completionProvider" in init_response["capabilities"]

        self.logger.log("LSP server initialized", logging.INFO)

        await self.server.notify.initialized({})
        self.logger.log(
            "LSP notified initialized.",
            logging.INFO,
        )

        '''
        self.completions_available.set()
        await self.service_ready_event.wait()

        '''

        async with super().start_server():
            if self.config.is_server:
                compile_commands_json_path = pathlib.Path(self.repository_root_path) / "compile_commands.json"
                with compile_commands_json_path.open("r") as f:
                    for cmd in json.load(f):
                        path = pathlib.Path(cmd["directory"]) / cmd["file"]
                        if path.exists():
                            # Trigger `didOpen` and `backgroundIndexProgress` when `self.config.is_server` is True
                            await self.request_definition(path.as_posix(), 0, 0)
                            await asyncio.sleep(1)
                            if self.indexing_end_event.is_set():
                                break
                await self.wait_for_indexing_completion()

            yield self

        await self.server.shutdown()
        await self.server.stop()

    async def wait_for_indexing_completion(self, timeout: float = 30):
        if self.indexing_end_event.is_set():
            self.logger.log("Clangd initial indexing was already marked as complete.", logging.INFO)
            return
        try:
            self.logger.log(f"Waiting for clangd indexing to begin (timeout: {timeout}s)...", logging.INFO)
            await asyncio.wait_for(self.indexing_begin_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.logger.log(f"Timeout waiting for clangd initial indexing after {timeout} seconds.", logging.INFO)
            # If the indexing begin event is not set within the timeout, we assume there will be no indexing
            self.logger.log(f"We will not wait for clangd indexing to begin after {timeout} seconds.", logging.INFO)
            self.indexing_end_event.set()
            return

        try:
            self.logger.log(f"Waiting for clangd indexing to end (timeout: {timeout}s)...", logging.INFO)
            await asyncio.wait_for(self.indexing_end_event.wait(), timeout=timeout)
            self.logger.log("Clangd initial indexing marked as complete by event.", logging.INFO)
        except asyncio.TimeoutError:
            self.logger.log(f"Timeout waiting for clangd initial indexing after {timeout} seconds.", logging.INFO)

    async def request_definition(
        self, relative_file_path: str, line: int, column: int
    ) -> List[multilspy_types.Location]:
        self.logger.log(f"Requesting definition for {relative_file_path}:{line}:{column} (is server? {self.config.is_server})", logging.INFO)
        if not self.indexing_end_event.is_set():
            # Trigger `didOpen` and `backgroundIndexProgress` when `self.config.is_server` is False
            await super().request_definition(relative_file_path, line, column)
            await self.wait_for_indexing_completion()

        return await super().request_definition(relative_file_path, line, column)