import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "bin" / "multilang_entrypoint"


class MultilangEntrypointTests(unittest.TestCase):
    def _write_executable(self, directory: Path, name: str, body: str) -> None:
        path = directory / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run_entrypoint(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ENTRYPOINT)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_init_codeindexer_exits_after_success_outside_oss_crs_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            fake_bin = tmpdir / "bin"
            fake_bin.mkdir()
            markers = tmpdir / "markers"
            markers.mkdir()

            self._write_executable(
                fake_bin,
                "init_codeindexer",
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    echo init > "{markers / 'init.txt'}"
                    """
                ),
            )
            self._write_executable(
                fake_bin,
                "tail",
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    echo "$@" > "{markers / 'tail.txt'}"
                    """
                ),
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["RUN_TYPE"] = "INIT_CODEINDEXER"
            env.pop("OSS_CRS_LOG_DIR", None)
            env.pop("INIT_CODEINDEXER_KEEPALIVE", None)

            result = self._run_entrypoint(env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((markers / "init.txt").read_text().strip(), "init")
            self.assertFalse((markers / "tail.txt").exists())

    def test_init_codeindexer_keeps_container_alive_during_oss_crs_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            fake_bin = tmpdir / "bin"
            fake_bin.mkdir()
            markers = tmpdir / "markers"
            markers.mkdir()

            self._write_executable(
                fake_bin,
                "init_codeindexer",
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    echo init > "{markers / 'init.txt'}"
                    """
                ),
            )
            self._write_executable(
                fake_bin,
                "tail",
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    echo "$@" > "{markers / 'tail.txt'}"
                    """
                ),
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["RUN_TYPE"] = "INIT_CODEINDEXER"
            env["OSS_CRS_LOG_DIR"] = str(tmpdir / "oss-crs-logs")
            env.pop("INIT_CODEINDEXER_KEEPALIVE", None)

            result = self._run_entrypoint(env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((markers / "init.txt").read_text().strip(), "init")
            self.assertEqual((markers / "tail.txt").read_text().strip(), "-f /dev/null")


if __name__ == "__main__":
    unittest.main()
