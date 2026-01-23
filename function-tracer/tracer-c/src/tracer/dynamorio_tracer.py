import os
import subprocess
import shutil
import random
import tarfile
import gc
import json

from typing import Iterator
from tempfile import TemporaryDirectory
from collections import defaultdict

from tracer.model import Relation, Relations, Caller, Callee, CallState, FunctionInfo

def safe_copytree(src, dst):
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)
        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(target_root, file)
            try:
                shutil.copy2(src_file, dst_file)
            except FileNotFoundError:
                pass


class DynamoRIOTracer:
    def __init__(
        self,
        harness_dir: str,
        copy: bool = False,
        dynamorio_home_path: str | None = None,
        dynamorio_plugin_path: str | None = None,
    ) -> None:
        if not copy:
            self.fuzzing_resource_dir = os.path.abspath(
                os.getenv("MULTILANG_BUILD_DIR")
            )
        self.workdir = self._initialize_trace_workdir(harness_dir, copy=copy)
        if dynamorio_home_path == None:
            dynamorio_home = os.path.abspath(
                os.getenv("CRS_SARIF_TRACER_DYNAMORIO_HOME")
            )
        else:
            dynamorio_home = dynamorio_home_path

        self.dynamorio_engine_path = os.path.join(dynamorio_home, "bin64", "drrun")

        if dynamorio_plugin_path == None:
            self.dynamorio_plugin_path = os.getenv(
                "CRS_SARIF_TRACER_FUNCTION_TRACER_PLUGIN"
            )
        else:
            self.dynamorio_plugin_path = dynamorio_plugin_path

        self._global_callstack: list[FunctionInfo, int] = list()
        self.trace_output_dir = TemporaryDirectory()
        self.trace_output_path = os.path.join(self.trace_output_dir.name, "trace.out")

    def _initialize_trace_workdir(self, harness_dir: str, copy: bool) -> str:
        tempdir = "".join(["%02x" % random.randint(0, 256) for _ in range(32)])
        tempdir = os.path.join("/tmp", tempdir)

        # shutil.copytree(harness_dir, tempdir)

        if copy:
            if os.path.exists(tempdir):
                shutil.rmtree(tempdir)
            safe_copytree(harness_dir, tempdir)
            return tempdir

        if not os.path.exists(tempdir):
            os.makedirs(tempdir)

        with tarfile.open(
            os.path.join(self.fuzzing_resource_dir, "fuzzers.tar.gz"), "r:*"
        ) as tar:
            tar.extractall(path=tempdir)

        return tempdir

    def _trim_trace_data(self, trace_lines: Iterator[str]) -> Iterator[str]:
        prev_line = None
        for line in trace_lines:
            line = line.replace("\n", "")
            if line and line != prev_line:
                prev_line = line
                yield line

    def _parse_caller_raw_info(self, raw_info: str) -> Caller:
        return Caller(
            file=raw_info.split(",")[0],
            line=-1,
            function_name=raw_info.split(",")[2],
        )

    def _parse_callee_raw_info(self, raw_info: str) -> Callee:
        return Callee(
            file=raw_info.split(",")[0],
            line=int(raw_info.split(",")[1], 10),
            function_name=raw_info.split(",")[2],
        )

    def _parse_callstate_raw_info(self, raw_info: str, callee: Callee) -> CallState:
        return CallState(
            file=raw_info.split(",")[0],
            line=int(raw_info.split(",")[1], 10),
            callee=callee,
        )

    def _calc_global_callstack(
        self, caller: FunctionInfo, callee: FunctionInfo, caller_called_lineno: int
    ) -> None:
        if len(self._global_callstack) == 0:
            self._global_callstack.append([callee, -1])
            return

        for stack_idx, function in reversed(list(enumerate(self._global_callstack))):
            if (
                function[0].function_name == caller.function_name
                and function[0].file == caller.file
            ):
                if function[1] == -1:
                    latest_caller_index = stack_idx
                    self._global_callstack[stack_idx][1] = caller_called_lineno
                    break
                elif function[1] == caller_called_lineno:
                    latest_caller_index = stack_idx
                    break
                else:
                    continue

        else:
            # TODO: Is this strategy is robust? (don't have false positive?)
            self._global_callstack.append([callee, -1])
            return

        self._global_callstack = self._global_callstack[: latest_caller_index + 1]
        self._global_callstack.append([callee, -1])

    def _get_caller_from_global_callstack(self) -> Caller | None:
        if len(self._global_callstack) < 2:
            return None
        return self._global_callstack[-2][0]

    def parse_raw_trace_data_for_edges(self) -> Relations:
        """Memory-efficient parsing of trace file for edges"""
        trace_file_path = self.trace_output_path

        # NOTE: Raw trace data is like:
        # caller-filename:caller-lineno:caller-function-name
        # |-->|
        # callee-filename:callee-lineno:callee-function-name
        caller_map = {}  # Dictionary to map caller to relation index

        relations = Relations()

        with open(trace_file_path, "r") as f:
            for line in self._trim_trace_data(f):
                thread_id_str, callstate_raw_info, callee_raw_info = line.split("|-->|")
                caller = self._parse_caller_raw_info(callstate_raw_info)
                callee = self._parse_callee_raw_info(callee_raw_info)
                callstate = self._parse_callstate_raw_info(callstate_raw_info, callee)

                if caller in caller_map:
                    relation_idx = caller_map[caller]
                    relation = relations[relation_idx]

                    if not callstate in relation.callees:
                        relation.callees.append(callstate)

                else:
                    relations.append(Relation(caller=caller, callees=[callstate]))

        return relations

    def parse_raw_data_for_trace(self) -> dict[int, Relations]:
        """Memory-efficient parsing of trace file for trace data"""
        trace_file_path = self.trace_output_path
        calltraces: dict[int, Relations] = defaultdict(Relations)

        with open(trace_file_path, "r") as f:
            for line in f:
                line = line.replace("\n", "")
                if not line:
                    continue

                thread_id_str, callstate_raw_info, callee_raw_info = line.split("|-->|")
                thread_id = int(thread_id_str, 10)
                caller = self._parse_caller_raw_info(callstate_raw_info)
                callee = self._parse_callee_raw_info(callee_raw_info)
                callstate = self._parse_callstate_raw_info(callstate_raw_info, callee)

                # TODO: Can we parse untracked function information?
                calltraces[thread_id].append(
                    Relation(caller=caller, callees=[callstate])
                )

        return calltraces

    def parse_raw_data_for_trace_direct_dump(
        self, output_file: str
    ) -> dict[int, Relations]:
        """Memory-efficient parsing of trace file for trace data"""
        trace_file_path = self.trace_output_path
        calltraces: dict[int, Relations] = defaultdict(Relations)

        with TemporaryDirectory() as temp_dir:
            thread_files = dict()
            line_count = 0

            with open(trace_file_path, "r") as f:
                for line in f:
                    if not line:
                        continue
                    line = line.replace("\n", "")
                    thread_id_str, callstate_raw_info, callee_raw_info = line.split(
                        "|-->|"
                    )
                    thread_filename = os.path.join(temp_dir, thread_id_str)

                    if thread_filename not in thread_files:
                        thread_file_fp = open(thread_filename, "w")
                        thread_files[thread_filename] = thread_file_fp
                    else:
                        thread_file_fp = thread_files[thread_filename]

                    caller = self._parse_caller_raw_info(callstate_raw_info)
                    callee = self._parse_callee_raw_info(callee_raw_info)
                    callstate = self._parse_callstate_raw_info(
                        callstate_raw_info, callee
                    )

                    thread_file_fp.write(
                        Relation(caller=caller, callees=[callstate]).model_dump_json()
                    )
                    thread_file_fp.write("\n")
                    thread_file_fp.flush()  # Force flush to disk

                    line_count += 1
                    if line_count % 10000 == 0:  # Every 10k lines
                        gc.collect()  # Force garbage collection

            # Close all thread files before reading them
            for thread_file_fp in thread_files.values():
                thread_file_fp.close()

            line_count = 0
            tracing_file_path = f"{output_file}.tracing"
            with open(tracing_file_path, "w") as final_f:
                final_f.write("{")

                thread_items = list(thread_files.keys())
                for i, thread_filename in enumerate(thread_items):
                    thread_id = os.path.basename(thread_filename)
                    final_f.write(f'"{thread_id}":[')

                    first_element = True
                    with open(thread_filename, "r") as thread_f:
                        for line in thread_f:
                            line = line.replace("\n", "")
                            if line:
                                if not first_element:
                                    final_f.write(",")
                                final_f.write(line)
                                first_element = False
                            thread_f.flush()
                            final_f.flush()
                            line_count += 1
                            if line_count % 10000 == 0:  # Every 10k lines
                                gc.collect()  # Force garbage collection
                    final_f.write("]")
                    if i < len(thread_items) - 1:
                        final_f.write(",")

                final_f.write("}")

            shutil.move(tracing_file_path, output_file)

    def parse_raw_data_for_trace_direct_dump_jsonl(
        self, output_file: str
    ) -> dict[int, Relations]:
        """Memory-efficient parsing of trace file for trace data"""
        trace_file_path = self.trace_output_path
        calltraces: dict[int, Relations] = defaultdict(Relations)

        tracing_file_path = f"{output_file}.tracing"
        with open(trace_file_path, "r") as f, open(tracing_file_path, "w") as output_f:
            for line in f:
                if not line:
                    continue
                line = line.replace("\n", "")

                thread_id_str, callstate_raw_info, callee_raw_info = line.split("|-->|")

                caller = self._parse_caller_raw_info(callstate_raw_info)
                callee = self._parse_callee_raw_info(callee_raw_info)
                callstate = self._parse_callstate_raw_info(callstate_raw_info, callee)

                output_f.write(
                    json.dumps(
                        {
                            "thread_id": thread_id_str,
                            "call_info": Relation(
                                caller=caller, callees=[callstate]
                            ).model_dump(),
                        }
                    )
                )
                output_f.write("\n")

        shutil.move(tracing_file_path, output_file)

    def trace(self, harness: str, input_data: bytes) -> None:
        harness_path = os.path.join(self.workdir, harness)

        temp_dir = self.trace_output_dir.name
        temp_input_path = os.path.join(temp_dir, "input")
        temp_trace_output_path = self.trace_output_path

        with open(temp_input_path, "wb") as f:
            f.write(input_data)

        cmd = [
            self.dynamorio_engine_path,
            "-disable_traces",
            "-c",
            self.dynamorio_plugin_path,
            temp_trace_output_path,
            "--",
            harness_path,
            "-runs=1",
            temp_input_path,
        ]
        subprocess.run(
            cmd,
            # capture_output=True,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )

        return

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir)
        self.trace_output_dir.cleanup()
