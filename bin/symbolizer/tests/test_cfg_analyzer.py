import multiprocessing
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import line_profiler

from llvm_symbolizer import LLVMSymbolizer
from symbolizer.cfg_analyzer import CFGAnalyzer


class TestCoverageTranslator(unittest.TestCase):
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
                ("mock-c", "git@github.com:Team-Atlanta/mock-c.git"): [
                    "ossfuzz-1",
                    "ossfuzz-2",
                ],
                ("simple-switch", "git@github.com:Team-Atlanta/simple-switch.git"): [
                    "ossfuzz-1",
                ],
                (
                    "asc-nginx",
                    "https://github.com/aixcc-public/challenge-004-nginx-source",
                ): ["smtp_harness", "pov_harness", "mail_request_harness"],
            },
            "cpp": {
                ("mock-cpp", "git@github.com:Team-Atlanta/mock-cpp.git"): [
                    "ossfuzz-1",
                    "ossfuzz-2",
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

    def test_run_objdump(self):
        test_dir = Path(__file__).parent.as_posix()
        for language, test_benchmarks in self.harness_binaries.items():
            for (benchmark, _), harnesses in test_benchmarks.items():
                repo_dir = os.path.join(self.workdir, benchmark)
                os.environ["CP_PROJ_PATH"] = os.path.join(
                    self.ossfuzz_repo_dir, "projects", "aixcc", language, benchmark
                )
                os.environ["CP_SRC_PATH"] = repo_dir
                for harness in harnesses:
                    test_file = os.path.join(
                        test_dir, "test_cases", benchmark, harness, harness
                    )
                    llvm_symbolizer = os.path.join(
                        test_dir, "test_cases", benchmark, "llvm-symbolizer"
                    )
                    profiler = line_profiler.LineProfiler()
                    profiler.add_function(CFGAnalyzer.__init__)
                    profiler.enable()
                    cfg_analyzer = CFGAnalyzer(
                        test_file, llvm_symbolizer, multiprocessing.cpu_count()
                    )
                    profiler.disable()
                    print(f"Profiling CFG Analyzer __init__ for {benchmark} {harness}")
                    profiler.print_stats()

                    # for function_cfg in cfg_analyzer.cfg:
                    #     if function_cfg.name in ["ngx_hash_init"]:
                    #         function_cfg.print_graph(benchmark, harness)


if __name__ == "__main__":
    unittest.main()
