#!/usr/bin/env python3

import argparse
import concurrent.futures
import glob
import json
import os.path
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import clang.cindex
from symbolizer import BinSymbolizer
from utils import get_new_file_path, is_running_under_pytest, map_lines_to_functions

clang.cindex.Config.set_library_file("/usr/lib/llvm-14/lib/libclang.so.1")


class HarnessCoverageRunner:
    def __init__(
        self,
        config: str,
        harness: str,
        work_dir: str,
        out_dir: str,
        disable_fallback: bool,
        log_dir: Optional[str],
    ):
        self.harness = harness
        self.work_dir = work_dir
        self.out_dir = out_dir
        self.disable_fallback = disable_fallback
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir

        self.target = os.path.basename(self.harness)

        self.dumps_dir = os.path.join(self.work_dir, "dumps")
        self.fuzzers_coverage_dumps_dir = os.path.join(
            self.work_dir, "dumps", "fuzzers_coverage"
        )
        self.merged_coverage_dir = os.path.join(self.work_dir, "merged_coverage")
        self.fuzzer_stats_dir = os.path.join(self.work_dir, "fuzzer_stats")
        self.textcov_report_dir = os.path.join(self.work_dir, "textcov_reports")
        self.logs_dir = os.path.join(self.work_dir, "logs")
        self.report_root_dir = os.path.join(self.work_dir, "report")
        self.report_by_target_root_dir = os.path.join(self.work_dir, "report_target")
        self.report_platform_dir = os.path.join(self.work_dir, "report", "linux")
        self.corpus_dir = os.path.join(self.work_dir, "corpus")

        self.profile_file = os.path.join(self.dumps_dir, "merged.profdata")
        self.summary_file = os.path.join(self.report_platform_dir, "summary.json")
        self.coverage_target_file = os.path.join(
            self.fuzzer_stats_dir, "coverage_targets.txt"
        )

        self.path_equivalence_args = f"-path-equivalence=/,{self.out_dir}"
        self.llvm_cov_common_args = (
            f"{self.path_equivalence_args} -ignore-filename-regex=.*src/libfuzzer/.*"
        )
        self.branch_cov_args = "--show-branches=count --show-expansions"
        self.time_out = "1h"

        self.cache: Dict[str, Dict[int, str]] = {}
        self.file_path_cache: Dict[str, str] = {}

        self.project_root = os.getenv("CP_PROJ_PATH", "/src")
        self.src_root = os.getenv("CP_SRC_PATH", "/src/repo")

        self.bin_symbolizer = (
            None
            if self.disable_fallback
            else BinSymbolizer(json.loads(Path(config).read_text()))
        )

    def initialize_directories(self) -> None:
        for dir_path in [
            self.dumps_dir,
            self.fuzzers_coverage_dumps_dir,
            self.merged_coverage_dir,
            self.fuzzer_stats_dir,
            self.textcov_report_dir,
            self.logs_dir,
            self.report_root_dir,
            self.report_by_target_root_dir,
            self.report_platform_dir,
            self.corpus_dir,
        ]:
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
            os.makedirs(dir_path, exist_ok=True)
            os.system(f"chmod -R a+wt '{dir_path}'")

    def _get_error_log_file_name(self, input_file: str) -> str:
        assert self.log_dir, "Log dir must not be None"
        return os.path.join(self.log_dir, f"{os.path.basename(input_file)}.error")

    def _error(
        self,
        error_message: str,
        input_file: Optional[str],
        exception: Optional[Exception],
        files_to_dump: List[str],
    ):
        if self.log_dir and input_file:
            log_file = self._get_error_log_file_name(input_file)
            with open(log_file, "a") as f:
                f.write(f"Error : {error_message} \n")
                if exception:
                    f.write("\n--- Exception Occurred ---\n")
                f.write(traceback.format_exc())
                f.write(f"\n--- Files to dump ---\n")
                f.write(f"{files_to_dump} \n")
                for dump_file in files_to_dump:
                    if not os.path.exists(dump_file):
                        f.write(
                            f"\n--- File {dump_file} does NOT EXIST!!!!!! CRITICAL ERROR ---\n"
                        )
                        continue
                    f.write(f"\n--- File {dump_file} ---\n")
                    with open(dump_file, "rb") as f_in:
                        offset = 0
                        while chunk := f_in.read(16):
                            hex_part = " ".join(f"{b:02x}" for b in chunk)
                            ascii_part = "".join(
                                chr(b) if 32 <= b < 127 else "." for b in chunk
                            )
                            f.write(f"{offset:08x}  {hex_part:<48}  {ascii_part}\n")
                            offset += 16

                            if offset >= 0x100:
                                break
                    f.write(f"\n-----------------------\n")
                f.write("\n\n\n\n")

    def run_harness_coverage(self, input_file: str) -> Tuple[Optional[str], List[str]]:
        self.initialize_directories()

        harness = self.harness
        target = self.target
        profraw_file = os.path.join(self.dumps_dir, f"{target}.%1m.profraw")
        profraw_file_mask = os.path.join(self.dumps_dir, f"{target}.*.profraw")
        profdata_file = os.path.join(self.dumps_dir, f"{target}.profdata")

        env = os.environ.copy()
        env["LLVM_PROFILE_FILE"] = profraw_file
        env["OUT"] = str(self.out_dir)
        env["TESTCASE"] = input_file
        harness_name = Path(self.harness).name

        # cmd = [
        #     "su",
        #     "fuzzer",
        #     "-c",
        #     f"reproduce '{harness_name}' -merge=1 -timeout=100",
        # ]
        cmd = ["reproduce", harness_name, "-merge=1", "-timeout=100"]

        try:
            subprocess.run(
                cmd,
                cwd=self.out_dir,
                env=env,
                shell=False,
                timeout=int(self.time_out[:-1]) * 3600,  # Convert hours to seconds
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception as e:
            self._error("Harness coverage run failed", input_file, e, [input_file])

        profraw_files = glob.glob(profraw_file_mask)
        if not profraw_files:
            self._error("Profraw file was not produced", input_file, None, [input_file])
            return None, []

        # for raw in profraw_files:
        #     try:
        #         os.chown(raw, 0, 0)
        #     except Exception as e:
        #         self._error(f"Failed to chown {raw}", input_file, e, [raw])

        if all(os.path.getsize(f) == 0 for f in profraw_files):
            self._error(
                "All profraw files are empty",
                input_file,
                None,
                [input_file] + profraw_files,
            )
            return None, profraw_files

        if "@" in target:
            target = target.split("@")[0]

        subprocess.run(
            f"profraw_update.py {os.path.join(self.out_dir, target)} -i {profraw_file_mask}",
            cwd=self.out_dir,
            shell=True,
            check=True,
        )

        subprocess.run(
            f"llvm-profdata merge -j=1 -sparse {profraw_file_mask} -o {profdata_file}",
            cwd=self.out_dir,
            shell=True,
            check=True,
        )

        shared_libs = subprocess.check_output(
            f"coverage_helper shared_libs -build-dir={self.out_dir} -object={target}",
            cwd=self.out_dir,
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        result = subprocess.run(
            f"llvm-cov-custom show -instr-profile={profdata_file} -object={target} "
            f"{shared_libs} {self.branch_cov_args} "
            f"{self.llvm_cov_common_args}",
            cwd=self.out_dir,
            shell=True,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout, [profdata_file]

    def adjust_file_path(self, file_path: str) -> str:
        return f"{self.out_dir}{file_path}"

    def _real_src_path(self, path_from_build: str) -> str:
        if path_from_build in self.file_path_cache:
            return self.file_path_cache[path_from_build]
        ret = get_new_file_path(path_from_build, self.project_root)
        if ret != None:
            self.file_path_cache[path_from_build] = ret
            return ret
        ret = get_new_file_path(path_from_build, self.src_root)
        if ret != None:
            self.file_path_cache[path_from_build] = ret
            return ret
        if is_running_under_pytest():
            path = Path(path_from_build).resolve()
            if not str(path).startswith("/usr/local/include") and not str(
                path
            ).startswith("/usr/include"):
                raise FileNotFoundError(
                    f"File not in project or repo: {path_from_build}"
                )
        self.file_path_cache[path_from_build] = path_from_build
        return path_from_build

    def get_coverage(self, input_file: str, raw_cov_file: str, output_file: str):
        text_cov, prof_files = self.run_harness_coverage(input_file)
        if text_cov is None:
            self._error(
                "text_cov is None",
                input_file,
                None,
                files_to_dump=[input_file, raw_cov_file] + prof_files,
            )
        covs = {}
        try:
            if text_cov:
                for result in re.split(r"\n{2,}", text_cov):
                    if result.strip() == "":
                        continue
                    lines = result.split("\n")

                    if len(lines) != 2:
                        continue

                    file_path = lines[0].strip()[:-1]
                    file_path = self.adjust_file_path(file_path)

                    if file_path not in self.cache:
                        self.cache[file_path] = map_lines_to_functions(file_path)

                    for line_number_str in lines[1].strip().split():
                        line_number = int(line_number_str)
                        if line_number not in self.cache[file_path]:
                            if is_running_under_pytest():
                                raise Exception(
                                    f"Unexpected line number: {line_number} in {file_path}"
                                )
                            continue
                        else:
                            func_name = self.cache[file_path][line_number]
                            if func_name not in covs:
                                covs[func_name] = {
                                    "src": self._real_src_path(file_path),
                                    "lines": [line_number],
                                }
                            else:
                                if line_number not in covs[func_name]["lines"]:
                                    covs[func_name]["lines"].append(line_number)

            for func_name, data in covs.items():
                data["lines"].sort()
        except Exception as e:
            self._error(
                "Error in parsing",
                input_file,
                e,
                [input_file, raw_cov_file] + prof_files,
            )
        finally:
            # if self.log_dir:
            #     keys = ["LLVMFuzzerTestOneInput", "DEFINE_PROTO_FUZZER"]
            #     if all(key not in covs for key in keys):
            #         self._error(
            #             f"Keys do not exist. {covs}",
            #             input_file,
            #             None,
            #             [input_file, raw_cov_file] + prof_files,
            #         )
            #         os.makedirs(f"/{os.path.basename(input_file)}")
            #         shutil.copy(
            #             input_file,
            #             f"/{os.path.basename(input_file)}/{os.path.basename(input_file)}",
            #         )
            #         for prof_file in prof_files:
            #             shutil.copy(
            #                 prof_file,
            #                 f"/{os.path.basename(input_file)}/{os.path.basename(prof_file)}",
            #             )
            if not covs and not text_cov and self.bin_symbolizer:
                self.bin_symbolizer.symbolize(raw_cov_file, output_file)
            else:
                with open(output_file, "wt") as f:
                    f.write(json.dumps(covs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arguments for cfg analyzer")

    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the config file (must have)",
    )
    parser.add_argument(
        "--coverage_harness",
        type=str,
        required=True,
        help="Path to the coverage harness (must have)",
    )
    parser.add_argument(
        "--work_dir",
        type=str,
        required=True,
        help="Path to the work directory that only this process uses (must have)",
    )
    parser.add_argument(
        "--out_dir",
        default="/linecov-out",
        help="Path to the out directory (default: /linecov-out)",
    )
    parser.add_argument(
        "--disable_fallback",
        action="store_true",
        help="Disable BinSymbolizer Fallback (default: False).",
    )
    parser.add_argument(
        "--log_dir",
        type=str,
        default=None,
        help="Path to the log dir (default: None).",
    )
    args = parser.parse_args()
    harness = HarnessCoverageRunner(
        args.config,
        args.coverage_harness,
        args.work_dir,
        args.out_dir,
        args.disable_fallback,
        args.log_dir,
    )

    timeout_seconds = (
        9 * 60 + 30
    )  # 9 minutes 30 seconds (30 seconds shorter than uniafl timeout)

    while True:
        input_file = input()
        raw_cov_file = input()
        output_file = raw_cov_file + ".cov"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(
                    harness.get_coverage, input_file, raw_cov_file, output_file
                )
                future.result(timeout=timeout_seconds)
            except Exception as e:
                if self.bin_symbolizer:
                    self.bin_symbolizer.symbolize(raw_cov_file, output_file)
                else:
                    with open(output_file, "wt") as f:
                        f.write(json.dumps({}))
            finally:
                print("DONE", flush=True)
