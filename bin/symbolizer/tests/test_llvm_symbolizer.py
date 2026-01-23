import os
import shutil
import subprocess
import unittest
from pathlib import Path

import line_profiler

from symbolizer.llvm_symbolizer import LLVMSymbolizer


class TestLLVMSymbolizer(unittest.TestCase):
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
                (
                    "asc-nginx",
                    "https://github.com/aixcc-public/challenge-004-nginx-source",
                ): [
                    "pov_harness",
                ],
            },
        }

        for _, test_benchmarks in self.harness_binaries.items():
            for (benchmark, repo_url), _ in test_benchmarks.items():
                repo_dir = os.path.join(self.workdir, benchmark)
                subprocess.check_call(["git", "clone", repo_url, repo_dir])

    def tearDown(self):
        shutil.rmtree(self.workdir)
        for env_key in ["CP_PROJ_PATH", "CP_SRC_PATH"]:
            if env_key in os.environ:
                del os.environ[env_key]

    def test_run_llvm_symbolizer_addr(self):
        test_dir = Path(__file__).parent.as_posix()
        for language, test_benchmarks in self.harness_binaries.items():
            for (benchmark, _), harnesses in test_benchmarks.items():
                repo_dir = os.path.join(self.workdir, benchmark)
                os.environ["CP_PROJ_PATH"] = os.path.join(
                    self.ossfuzz_repo_dir, "projects", "aixcc", language, benchmark
                )
                os.environ["CP_SRC_PATH"] = repo_dir
                for harness in harnesses:
                    harness_file = os.path.join(
                        test_dir, "test_cases", benchmark, harness, harness
                    )
                    llvm_symbolizer = os.path.join(
                        test_dir, "test_cases", benchmark, "llvm-symbolizer"
                    )
                    input_file = os.path.join(
                        test_dir, "test_cases", benchmark, harness, f"{harness}.txt"
                    )
                    addrs = []
                    with open(input_file, "rt") as f:
                        addrs = [
                            int(line.strip(), 16)
                            for line in f.readlines()
                            if line != "\n"
                        ]
                    llvm_symbolizer = LLVMSymbolizer(harness_file, llvm_symbolizer)
                    profiler = line_profiler.LineProfiler()
                    profiler.add_function(LLVMSymbolizer.run_llvm_symbolizer_addr)
                    profiler.enable()
                    for addr in addrs:
                        llvm_symbolizer.run_llvm_symbolizer_addr(addr)
                    profiler.disable()
                    print(
                        f"Profiling LLVM Symbolizer __init__ for {benchmark} {harness}"
                    )
                    profiler.print_stats()

    def test_run_llvm_symbolizer_addrs(self):
        test_dir = Path(__file__).parent.as_posix()
        for language, test_benchmarks in self.harness_binaries.items():
            for (benchmark, _), harnesses in test_benchmarks.items():
                repo_dir = os.path.join(self.workdir, benchmark)
                os.environ["CP_PROJ_PATH"] = os.path.join(
                    self.ossfuzz_repo_dir, "projects", "aixcc", language, benchmark
                )
                os.environ["CP_SRC_PATH"] = repo_dir
                for harness in harnesses:
                    harness_file = os.path.join(
                        test_dir, "test_cases", benchmark, harness, harness
                    )
                    llvm_symbolizer = os.path.join(
                        test_dir, "test_cases", benchmark, "llvm-symbolizer"
                    )
                    input_file = os.path.join(
                        test_dir, "test_cases", benchmark, harness, f"{harness}.txt"
                    )
                    addrs = []
                    with open(input_file, "rt") as f:
                        addrs = [
                            int(line.strip(), 16)
                            for line in f.readlines()
                            if line != "\n"
                        ]
                    llvm_symbolizer = LLVMSymbolizer(harness_file, llvm_symbolizer)
                    profiler = line_profiler.LineProfiler()
                    profiler.add_function(LLVMSymbolizer.run_llvm_symbolizer_addrs)
                    profiler.enable()
                    llvm_symbolizer.run_llvm_symbolizer_addrs(addrs)
                    profiler.disable()
                    print(
                        f"Profiling LLVM Symbolizer __init__ for {benchmark} {harness}"
                    )
                    profiler.print_stats()


if __name__ == "__main__":
    unittest.main()
