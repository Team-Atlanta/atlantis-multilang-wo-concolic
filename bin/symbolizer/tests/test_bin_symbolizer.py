import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from symbolizer.symbolizer import BinSymbolizer


class TestBinSymbolizer(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(__file__).parent.as_posix()
        self.workdir = os.path.join("/tmp", "work")
        shutil.rmtree(self.workdir, ignore_errors=True)
        self.ossfuzz_repo_url = "git@github.com:Team-Atlanta/oss-fuzz.git"
        self.ossfuzz_repo_dir = os.path.join(self.workdir, "oss-fuzz")
        subprocess.check_call(
            ["git", "clone", self.ossfuzz_repo_url, self.ossfuzz_repo_dir]
        )
        self.harness_binaries = {
            "c": {
                ("mock-c", "git@github.com:Team-Atlanta/mock-c.git"): {
                    "ossfuzz-1": ["622183a76abd530d"],
                    "ossfuzz-2": [
                        "2776568e20d68d2c",
                        "d71523fb4cbdbdfa",
                        "55fb265d2166fa4a",
                        "acc541f99bab0a08",
                    ],
                }
            },
            "cpp": {
                ("mock-cpp", "git@github.com:Team-Atlanta/mock-cpp.git"): {
                    "ossfuzz-1": [],
                    "ossfuzz-2": ["3b4037455b8845a5"],
                }
            },
        }
        for language, test_benchmarks in self.harness_binaries.items():
            for (benchmark, _), test_cases in test_benchmarks.items():
                for harness, _ in test_cases.items():
                    harness_path = os.path.join(
                        self.test_dir, "test_cases", benchmark, harness, harness
                    )

                    config_data = {"harness_path": harness_path, "language": language}
                    config_file_path = os.path.join(
                        self.test_dir, "test_cases", benchmark, harness, "config.json"
                    )
                    with open(config_file_path, "wt") as f:
                        f.write(json.dumps(config_data))

    def tearDown(self):
        shutil.rmtree(self.workdir)
        for env_key in ["CP_PROJ_PATH", "CP_SRC_PATH"]:
            if env_key in os.environ:
                del os.environ[env_key]
        for _, test_benchmarks in self.harness_binaries.items():
            for (benchmark, _), test_cases in test_benchmarks.items():
                for harness, _ in test_cases.items():
                    config_file_path = os.path.join(
                        self.test_dir, "test_cases", benchmark, harness, "config.json"
                    )
                    os.remove(config_file_path)

    def test_symbolize(self):
        for language, test_benchmarks in self.harness_binaries.items():
            for (benchmark, repo_url), test_cases in test_benchmarks.items():
                repo_dir = os.path.join(self.workdir, benchmark)
                subprocess.check_call(["git", "clone", repo_url, repo_dir])
                os.environ["CP_PROJ_PATH"] = os.path.join(
                    self.ossfuzz_repo_dir, "projects", "aixcc", language, benchmark
                )
                os.environ["CP_SRC_PATH"] = repo_dir

                for harness, cov_files in test_cases.items():
                    for cov_file in cov_files:
                        config_file_path = os.path.join(
                            self.test_dir,
                            "test_cases",
                            benchmark,
                            harness,
                            "config.json",
                        )
                        conf = json.loads(Path(config_file_path).read_text())
                        llvm_symbolizer_path = os.path.join(
                            self.test_dir, "test_cases", benchmark, "llvm-symbolizer"
                        )
                        cov_file_path = os.path.join(
                            self.test_dir, "test_cases", benchmark, harness, cov_file
                        )
                        output_file_path = cov_file_path + ".json"

                        symbolizer = BinSymbolizer(conf, llvm_symbolizer_path)
                        symbolizer.symbolize(cov_file_path, output_file_path)


if __name__ == "__main__":
    unittest.main()
