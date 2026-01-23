import argparse
from multilspy.multilspy_config import MultilspyConfig
from multilspy.language_server import LanguageServer
from multilspy.multilspy_logger import MultilspyLogger
from libCRS.challenge import init_cp_in_runner, CP
import asyncio
from loguru import logger
import shutil
from pathlib import Path
import re
import os
import subprocess
import json

def init_compile_db_json(cp: CP):
    """Process compile commands json file."""
    if not (cp.language == "c" or cp.language == "c++"):
        return

    compile_commands_json: Path = cp.cp_src_path / "compile_commands.json"

    if not compile_commands_json.exists():
        logger.error(
            f"Compile commands json file not found: {compile_commands_json}"
        )
        return

    backup_compile_commands_json = compile_commands_json.with_suffix(".json.bak")

    with open(compile_commands_json, "r") as f:
        orig_data = f.read()

    if not backup_compile_commands_json.exists():
        with open(backup_compile_commands_json, "w") as fw:
            fw.write(orig_data)
    else:
        logger.info(
            "Backup compile commands json file already exists:"
            f" {backup_compile_commands_json}"
        )

    # Prevent clangd from crashing when the one of command compile_commands.json file is not valid
    old_cp_src_path = None
    valid_cmds = []
    cmds = json.loads(orig_data)
    if isinstance(cmds, list):
        for cmd in cmds:
            if isinstance(cmd, dict) and "directory" in cmd:
                dir_path = Path(cmd["directory"])
                # /src/*/ -> /src/repo/
                if len(dir_path.parts) >= 3 and dir_path.parts[:2] == ("/", "src"):
                    tmp_old_cp_src_path = Path(*dir_path.parts[:3])
                    if old_cp_src_path is None:
                        old_cp_src_path = tmp_old_cp_src_path
                        logger.info(f"Found old cp src path: {old_cp_src_path}")
                        if old_cp_src_path.is_dir() and not old_cp_src_path.is_symlink():
                            try:
                                old_cp_src_path.rmdir()
                                os.symlink(
                                    cp.cp_src_path.as_posix(),
                                    old_cp_src_path.as_posix(),
                                    target_is_directory=True
                                )
                                logger.info(f"Created symlink for {old_cp_src_path} -> {cp.cp_src_path}")
                            except OSError as e:
                                logger.error(
                                    f"Failed to create symlink for {old_cp_src_path}: {e}"
                                )

                    if old_cp_src_path is not None:
                        if tmp_old_cp_src_path != old_cp_src_path:
                            logger.warning(f"Ignoring inconsistent directory paths found in compile_commands.json: {old_cp_src_path} and {tmp_old_cp_src_path}")
                        else:
                            dir_path = cp.cp_src_path / dir_path.relative_to(old_cp_src_path)

                if "file" in cmd:
                    file_path: Path = dir_path / cmd["file"]
                    if not dir_path.resolve().exists():
                        # LLVM ERROR: Cannot chdir into "/src/repo/build/CMakeFiles/CMakeScratch/TryCompile-WQbPzj"!
                        # Aborted (core dumped)
                        logger.warning(f"Ignoring non-existing dir {dir_path} in compile_commands.json")
                        continue
                    if not file_path.resolve().exists():
                        logger.warning(f"Ignoring non-existing path {file_path} in compile_commands.json")
                        continue

            valid_cmds.append(cmd)

    validated_compiled_commands_json = json.dumps(valid_cmds, indent=4)

    if old_cp_src_path is not None:
        new_lines = []
        for line in validated_compiled_commands_json.splitlines():
            line = line
            new_lines.append(line.replace(old_cp_src_path.as_posix(), cp.cp_src_path.as_posix()))
        validated_compiled_commands_json = "\n".join(new_lines)

    with open(compile_commands_json, "w") as fw:
        fw.write(validated_compiled_commands_json)

    logger.info(f"Processed compile commands json file: {compile_commands_json}")

    proj_compile_commands_json = cp.proj_path / "compile_commands.json"
    shutil.copy(compile_commands_json, proj_compile_commands_json)


async def run_server():
    cp = init_cp_in_runner()
    init_compile_db_json(cp)

    if cp.language == "c" or cp.language == "c++":
        language = "c"
    elif cp.language == "jvm":
        language = "java"
    else:
        language = cp.language
    
    config = MultilspyConfig.from_dict(
        {
            "code_language": language,
            # TODO: remove this for release
            # "trace_lsp_communication": True,
            "is_server": True,
            "is_offline": True,
        }
    )
    src_path = cp.cp_src_path.resolve().as_posix()
    msp_logger = MultilspyLogger()
    lsp = LanguageServer.create(config, msp_logger, src_path)

    async with lsp.start_server():
        logger.info("LSP server started")
    logger.info("LSP server initialized")
    while True:
        await asyncio.sleep(1000)

async def main():
    await run_server()

if __name__ == "__main__":
    asyncio.run(main())
