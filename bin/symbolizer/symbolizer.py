#!/usr/bin/env python3

import argparse
import concurrent.futures
import glob
import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List

from addr_line_mapper import AddrLineMapper


class Symbolizer(ABC):
    @abstractmethod
    def symbolize(self, cov_path: str, output_path: str):
        pass


class BinSymbolizer(Symbolizer):
    def __init__(self, conf: Any):
        self.conf = conf
        self.harness: str = self.conf["harness_path"]
        self.redis_url = conf["redis_url"]
        self.addr_line_mapper = AddrLineMapper(self.harness, self.redis_url)

    def symbolize(self, cov_path: str, output_path: str):
        covs = {}
        with open(cov_path, "rb") as f:
            addrs: List[int] = []
            while True:
                data = f.read(8)
                if not data:
                    break
                addr = int.from_bytes(data, byteorder="little") - 0x555555554000
                addrs.append(addr)

            line_infos = self.addr_line_mapper.translate(addrs)
            for line_info in line_infos:
                func_name = line_info.function_name
                src_name = line_info.src_file
                src_line = line_info.line_number

                if func_name not in covs:
                    covs[func_name] = {"src": src_name, "lines": [src_line]}
                else:
                    if src_line not in covs[func_name]["lines"]:
                        covs[func_name]["lines"].append(src_line)

        for func_name, data in covs.items():
            data["lines"].sort()

        with open(output_path, "wt") as f:
            f.write(json.dumps(covs))


class JvmSymbolizer(Symbolizer):
    def __init__(self, conf: Any):
        self.harness = conf["harness_path"].split("/")[-1]
        self.redis_url = conf["redis_url"]
        self.adjust_cache = {}
        self.bases = ["/src/"]
        self.dirs_in_src = []
        for path in glob.glob("/src/*"):
            if path != "/src/src" and Path(path).is_dir():
                self.dirs_in_src.append(path)
        self.proc = self.__run_symbolizer()

    def __run_symbolizer(self):
        cmd = [
            self.harness,
            "--uniafl_coverage",
            f"--redis_url={self.redis_url}",
            "--uniafl_dump",
        ]
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return process

    def __ensure_symbolizer(self):
        if self.proc.poll() != None:
            self.proc.wait()
            self.proc = self.__run_symbolizer()

    def symbolize(self, cov_path, output_path):
        self.__ensure_symbolizer()
        self.proc.stdin.write(bytes(str(cov_path) + "\n", "utf-8"))
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if b"UNIAFL_COV_DONE" in line:
                break
        self.__adjust_cov(cov_path + ".json", output_path)

    def __adjust_cov(self, cov_json_path, output_path):
        ret = {}
        with open(cov_json_path) as f:
            data = json.load(f)
            for func in data:
                src = data[func]["src"]
                src = self.__adjust_src_path(src)
                if src != None:
                    ret[func] = data[func]
                    ret[func]["src"] = src
        Path(cov_json_path).unlink(missing_ok=True)
        Path(str(output_path)).write_text(json.dumps(ret))

    def __adjust_src_path(self, subpath):
        if subpath in self.adjust_cache:
            return self.adjust_cache[subpath]
        ret = self.__resolve_subpath(subpath)
        self.adjust_cache[subpath] = ret
        return ret

    def __resolve_subpath(self, subpath):
        if subpath.startswith("/") and Path(subpath).exists():
            return subpath
        for base in self.bases:
            tmp = Path(f"{base}/{subpath}")
            if tmp.exists():
                return str(tmp)
        for dir in self.dirs_in_src:
            path = f"{dir}/{subpath}"
            if Path(path).exists():
                ret = [path]
            else:
                ret = glob.glob(f"{dir}/**/{subpath}", recursive=True)
            if len(ret) > 0:
                ret = ret[0]
                self.bases.append(ret[: -len(subpath)])
                return ret
        return None


def get_symbolizer(conf: Any) -> Symbolizer:
    lang = conf["language"]
    if lang in ["c", "cpp", "c++", "rust", "go"]:
        return BinSymbolizer(conf)
    return JvmSymbolizer(conf)


def main(conf_file: str) -> int:
    conf = json.loads(Path(conf_file).read_text())
    symbolizer = get_symbolizer(conf)
    """
    DO NOT TOUCH THIS LOGIC.
    """

    timeout_seconds = (
        9 * 60 + 30
    )  # 9 minutes 30 seconds (30 seconds shorter than uniafl timeout)

    while True:
        _ = input()
        cov_path = input()
        output_path = cov_path + ".cov"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(symbolizer.symbolize, cov_path, output_path)
            try:
                future.result(timeout=timeout_seconds)
            except Exception:
                with open(output_path, "wt") as f:
                    f.write(json.dumps({}))
            finally:
                print("DONE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("conf_file", help="Path to the configuration file")

    args = parser.parse_args()

    sys.exit(main(args.conf_file))
