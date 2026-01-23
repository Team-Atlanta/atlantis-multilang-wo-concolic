import re
import shutil
from pathlib import Path

import pytest
from loguru import logger
from multilspy import LanguageServer, SyncLanguageServer
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

pytestmark = pytest.mark.skip(reason="This test is not for mlla.")


def replace_compile_db(old: Path, dest_dir: Path):
    new = dest_dir / "repo" / "compile_commands.json"
    new2 = dest_dir / "compile_commands.json"
    if not (old.exists() and not new.exists()):
        return

    # pattern = r'"/src/[^/"]+(?=[/"])'
    # pattern = r'((?:^|["\s])(?:-I)?)\/src\/[^\s"]+(?=["\s]|$)'
    pattern = r'((?:^|["\s])(?:-I)?)/src/([^/\s"]+)([^\s"]*)'

    with open(old, "r") as f:
        data = f.read().splitlines()

        new_data = []

        new_dir = dest_dir / "repo"

        for line in data:
            if re.search(pattern, line):
                logger.info(f"line: {line}")
                # line = re.sub(pattern, f'{dest_dir.as_posix()}/', line)
                line = re.sub(pattern, f"\\1{new_dir.as_posix()}\\3", line)
                new_data.append(line)
            else:
                new_data.append(line)

        with open(new, "w") as fw:
            fw.write("\n".join(new_data))

    shutil.copy(new, new2)


def test_multilspy_for_cpua_jenkins(cp_jenkins_path: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "java"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_jenkins_path = cp_jenkins_path.resolve()
    lsp = SyncLanguageServer.create(config, msp_logger, cp_jenkins_path.as_posix())

    with lsp.start_server():
        relative_file_path = (
            "fuzz/jenkins-harness-three/src/main/java/com/aixcc/jenkins/harnesses/"
            + "three/JenkinsThree.java"
        )
        file_path = (cp_jenkins_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 250, 24)
        assert result["contents"] == ""

        result = lsp.request_definition(file_path, 250, 24)
        assert result == []


def test_multilspy_for_mcga_jenkins(cp_jenkins_path: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "java"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_jenkins_path = cp_jenkins_path.resolve()
    lsp = SyncLanguageServer.create(config, msp_logger, cp_jenkins_path.as_posix())

    with lsp.start_server():
        relative_file_path = (
            "repo/plugins/toy-plugin/src/main/java/io/jenkins/plugins/"
            "toyplugin/AuthAction.java"
        )
        file_path = (cp_jenkins_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 223, 22)
        assert "AuthAction.isAdmin" in result["contents"]["value"]

        result = lsp.request_definition(file_path, 223, 22)
        assert len(result) == 1
        assert "AuthAction.java" in result[0]["uri"]


def test_multilspy_for_cpua(cp_batik_path: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "java"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_batik_path = cp_batik_path.resolve()
    lsp = SyncLanguageServer.create(config, msp_logger, cp_batik_path.as_posix())

    with lsp.start_server():
        relative_file_path = (
            "fuzz/batik-harness-one/src/main/java/com/aixcc/batik/harnesses/"
            + "one/BatikOne.java"
        )
        file_path = (cp_batik_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 66, 18)
        assert result["contents"] == ""

        result = lsp.request_hover(relative_file_path, 67, 18)
        assert "java.io.FileOutputStream.close()" in result["contents"]["value"]

        result = lsp.request_definition(file_path, 66, 18)
        assert result == []


def test_multilspy_for_cpua_nginx(cp_nginx_path: Path, oss_fuzz_workdir: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "c"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_nginx_path = cp_nginx_path.resolve() / "repo"
    lsp = SyncLanguageServer.create(config, msp_logger, cp_nginx_path.as_posix())
    compile_db_json = oss_fuzz_workdir / "aixcc/c/asc-nginx" / "compile_commands.json"
    new_compile_db_json = cp_nginx_path / "compile_commands.json"

    if compile_db_json.exists() and not new_compile_db_json.exists():
        import re

        dest_dir = cp_nginx_path

        with open(compile_db_json, "r") as f:
            data = f.read()

            pattern = r'"/src/[^/"]+(?=[/"])'
            data = re.sub(pattern, '"' + dest_dir.as_posix() + "/", data)

            with open(new_compile_db_json, "w") as fw:
                fw.write(data)

    with lsp.start_server():
        relative_file_path = "src/fuzz/smtp_harness.cc"
        file_path = (cp_nginx_path / relative_file_path).as_posix()

        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 204, 30)
        logger.info(f"result: {result}")

        result = lsp.request_hover(relative_file_path, 206, 30)
        logger.info(f"result: {result}")

        result = lsp.request_definition(file_path, 204, 30)
        logger.info(f"result: {result}")


def test_multilspy_for_cpua_libpng(cp_libpng_path: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "c"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_libpng_path = cp_libpng_path.resolve() / "repo"
    lsp = SyncLanguageServer.create(config, msp_logger, cp_libpng_path.as_posix())

    with lsp.start_server():
        relative_file_path = "contrib/oss-fuzz/libpng_read_fuzzer.cc"
        file_path = (cp_libpng_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 197, 11)
        logger.info(f"result: {result}")

        result = lsp.request_definition(file_path, 197, 11)
        logger.info(f"result: {result}")


def test_multilspy_for_mcga_libpng(cp_libpng_path: Path, oss_fuzz_workdir: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "c"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_libpng_path = cp_libpng_path.resolve() / "repo"
    lsp = SyncLanguageServer.create(config, msp_logger, cp_libpng_path.as_posix())

    compile_db_json = (
        oss_fuzz_workdir / "aixcc/cpp/example-libpng" / "compile_commands.json"
    )
    new_compile_db_json = cp_libpng_path / "compile_commands.json"

    if compile_db_json.exists() and not new_compile_db_json.exists():
        import re

        dest_dir = cp_libpng_path

        with open(compile_db_json, "r") as f:
            data = f.read()

            pattern = r'"/src/[^/"]+(?=[/"])'
            data = re.sub(pattern, '"' + dest_dir.as_posix() + "/", data)

            with open(new_compile_db_json, "w") as fw:
                fw.write(data)

    with lsp.start_server():
        relative_file_path = "png.c"
        file_path = (cp_libpng_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 2120, 11)
        logger.info(f"result: {result}")

        result = lsp.request_definition(file_path, 2120, 11)
        logger.info(f"result: {result}")


def test_multilspy_for_cpua_zstd(cp_zstd_path: Path, oss_fuzz_workdir: Path):
    config = MultilspyConfig.from_dict(
        {"code_language": "c"}
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    cp_zstd_path = cp_zstd_path.resolve() / "repo"
    compile_db_json = (
        oss_fuzz_workdir / "aixcc/cpp/zstd-16541" / "compile_commands.json"
    )
    new_compile_db_json = cp_zstd_path / "compile_commands.json"

    if compile_db_json.exists() and not new_compile_db_json.exists():
        import re

        dest_dir = cp_zstd_path

        with open(compile_db_json, "r") as f:
            data = f.read()

            pattern = r'"/src/[^/"]+(?=[/"])'
            data = re.sub(pattern, '"' + dest_dir.as_posix() + "/", data)

            with open(new_compile_db_json, "w") as fw:
                fw.write(data)

    lsp = SyncLanguageServer.create(config, msp_logger, cp_zstd_path.as_posix())

    with lsp.start_server():
        relative_file_path = "tests/fuzz/block_decompress.c"
        file_path = (cp_zstd_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        result = lsp.request_hover(relative_file_path, 43, 11)
        logger.info(f"result: {result}")

        result = lsp.request_definition(file_path, 43, 11)
        logger.info(f"result: {result}")


@pytest.mark.asyncio
async def test_multilspy_for_cpua_mockc(cp_mockc_path: Path, oss_fuzz_workdir: Path):
    lsp_config = MultilspyConfig.from_dict(
        {
            "code_language": "c",
            #  "trace_lsp_communication": True
        }
    )  # Also supports "python", "rust", "csharp", "typescript", "javascript",
    # "go", "dart", "ruby"
    msp_logger = MultilspyLogger()
    new_cp_mockc_path = cp_mockc_path.resolve() / "repo"
    compile_db_json = oss_fuzz_workdir / "aixcc/c/mock-c" / "compile_commands.json"

    replace_compile_db(compile_db_json, cp_mockc_path.resolve())

    lsp = LanguageServer.create(lsp_config, msp_logger, new_cp_mockc_path.as_posix())

    async with lsp.start_server():
        relative_file_path = "../fuzz/ossfuzz-1.c"
        file_path = (new_cp_mockc_path / relative_file_path).as_posix()
        logger.info(f"file_path: {file_path}")

        # mockc_file = new_cp_mockc_path / "mock.c"

        # with lsp.open_file(mockc_file.as_posix()):

        result = await lsp.request_hover(relative_file_path, 3, 3)
        logger.info(f"result: {result}")

        result = await lsp.request_definition(relative_file_path, 3, 3)
        logger.info(f"result: {result}")

    # new_compile_db_json = cp_mockc_path / "compile_commands.json"
    # if new_compile_db_json.exists():
    #     new_compile_db_json.unlink()
    # new_compile_db_json = new_cp_mockc_path / "compile_commands.json"
    # if new_compile_db_json.exists():
    #     new_compile_db_json.unlink()
