import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SCRIPT = (
    REPO_ROOT
    / "libs/oss-fuzz/infra/base-images/base-builder/export_libclang_rt_fuzzer.sh"
)
INSTALL_SCRIPT = REPO_ROOT / "oss-crs/dockerfiles/install_libclang_rt_fuzzer.sh"


class LibClangRuntimeScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="clang-runtime-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _run(self, cmd, env=None):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=True,
            env=merged_env,
        )

    def _make_runtime(self, major: str, content: str) -> Path:
        runtime = (
            self.tmpdir
            / "clang-root"
            / major
            / "lib"
            / "x86_64-unknown-linux-gnu"
            / "libclang_rt.fuzzer.a"
        )
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text(content)
        return runtime

    def _make_fake_clang(self, resource_dir: Path) -> Path:
        fake_clang = self.tmpdir / "bin" / "clang"
        fake_clang.parent.mkdir(parents=True, exist_ok=True)
        fake_clang.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--print-resource-dir" ]; then\n'
            f'  printf "%s\\n" "{resource_dir}"\n'
            "  exit 0\n"
            "fi\n"
            'printf "unexpected args: %s\\n" "$*" >&2\n'
            "exit 1\n"
        )
        fake_clang.chmod(0o755)
        return fake_clang

    def test_export_script_finds_clang_18_runtime(self):
        self._make_runtime("18", "clang18")
        output = self.tmpdir / "exported" / "libclang_rt.fuzzer.a"

        self._run(
            ["bash", str(EXPORT_SCRIPT), str(output)],
            env={"CLANG_LIB_ROOT": str(self.tmpdir / "clang-root")},
        )

        self.assertEqual(output.read_text(), "clang18")

    def test_export_script_finds_clang_22_runtime(self):
        self._make_runtime("22", "clang22")
        output = self.tmpdir / "exported" / "libclang_rt.fuzzer.a"

        self._run(
            ["bash", str(EXPORT_SCRIPT), str(output)],
            env={"CLANG_LIB_ROOT": str(self.tmpdir / "clang-root")},
        )

        self.assertEqual(output.read_text(), "clang22")

    def test_export_script_prefers_active_clang_in_mixed_layout(self):
        self._make_runtime("18", "clang18")
        self._make_runtime("22", "clang22")
        output = self.tmpdir / "exported" / "libclang_rt.fuzzer.a"
        fake_clang = self._make_fake_clang(self.tmpdir / "clang-root" / "22")

        self._run(
            ["bash", str(EXPORT_SCRIPT), str(output)],
            env={
                "CLANG_BIN": str(fake_clang),
                "CLANG_LIB_ROOT": str(self.tmpdir / "clang-root"),
                "TARGET_TRIPLE_DIR": "x86_64-unknown-linux-gnu",
            },
        )

        self.assertEqual(output.read_text(), "clang22")

    def test_export_script_rejects_ambiguous_layout_without_active_clang(self):
        self._make_runtime("18", "clang18")
        self._make_runtime("22", "clang22")
        output = self.tmpdir / "exported" / "libclang_rt.fuzzer.a"

        with self.assertRaises(subprocess.CalledProcessError):
            self._run(
                ["bash", str(EXPORT_SCRIPT), str(output)],
                env={
                    "CLANG_BIN": str(self.tmpdir / "missing-clang"),
                    "CLANG_LIB_ROOT": str(self.tmpdir / "clang-root"),
                    "TARGET_TRIPLE_DIR": "x86_64-unknown-linux-gnu",
                },
            )

    def test_install_script_uses_clang_18_resource_dir(self):
        archive = self.tmpdir / "archive" / "libclang_rt.fuzzer.a"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("clang18")
        resource_dir = self.tmpdir / "resource" / "clang" / "18"
        fake_clang = self._make_fake_clang(resource_dir)

        self._run(
            ["bash", str(INSTALL_SCRIPT), str(archive)],
            env={
                "CLANG_BIN": str(fake_clang),
                "TARGET_TRIPLE_DIR": "x86_64-unknown-linux-gnu",
            },
        )

        installed = (
            resource_dir
            / "lib"
            / "x86_64-unknown-linux-gnu"
            / "libclang_rt.fuzzer.a"
        )
        self.assertEqual(installed.read_text(), "clang18")

    def test_install_script_uses_clang_22_resource_dir(self):
        archive = self.tmpdir / "archive" / "libclang_rt.fuzzer.a"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("clang22")
        resource_dir = self.tmpdir / "resource" / "clang" / "22"
        fake_clang = self._make_fake_clang(resource_dir)

        self._run(
            ["bash", str(INSTALL_SCRIPT), str(archive)],
            env={
                "CLANG_BIN": str(fake_clang),
                "TARGET_TRIPLE_DIR": "x86_64-unknown-linux-gnu",
            },
        )

        installed = (
            resource_dir
            / "lib"
            / "x86_64-unknown-linux-gnu"
            / "libclang_rt.fuzzer.a"
        )
        self.assertEqual(installed.read_text(), "clang22")


if __name__ == "__main__":
    unittest.main()
