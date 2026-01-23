import os
import subprocess
import shutil
import gc
import json
from tempfile import TemporaryDirectory
from collections import defaultdict
from typing import Iterator
from tracer.model import Relation, Relations, Caller, Callee, CallState, MethodInfo


class JazzerTracer:
    def __init__(self, workdir: str) -> None:
        self.workdir = workdir
        self.trace_output_dir = TemporaryDirectory()
        self.trace_output_path = os.path.join(self.trace_output_dir.name, "trace.out")

    def _trim_trace_data(self, trace_lines: Iterator[str]) -> Iterator[str]:
        prev_line = None
        for line in trace_lines:
            line = line.replace("\n", "")
            if line and line != prev_line:
                prev_line = line
                yield line

    def _parse_caller_raw_info(self, raw_info: str) -> Caller:
        return Caller(
            file=raw_info.split(":")[0],
            class_name=raw_info.split(":")[2],
            method_name=raw_info.split(":")[3],
            prototype=raw_info.split(":")[4],
        )

    def _parse_callee_raw_info(self, raw_info: str) -> Callee:
        return Callee(
            file=raw_info.split(":")[0],
            class_name=raw_info.split(":")[1],
            method_name=raw_info.split(":")[2],
            prototype=raw_info.split(":")[3],
        )

    def _parse_callstate_raw_info(self, raw_info: str, callee: Callee) -> CallState:
        return CallState(
            file=raw_info.split(":")[0],
            line=int(raw_info.split(":")[1], 10),
            callee=callee,
        )

    def parse_raw_trace_data_for_edges(self) -> Relations:
        """Memory-efficient parsing of trace file for edges"""
        trace_file_path = self.trace_output_path
        relations = Relations()
        caller_map = {}  # Dictionary to map caller to relation index

        with open(trace_file_path, "r") as f:
            for line in self._trim_trace_data(f):
                thread_id_str, callstate_raw_info, callee_raw_info = line.split(",")

                caller = self._parse_caller_raw_info(callstate_raw_info)
                callee = self._parse_callee_raw_info(callee_raw_info)
                callstate = self._parse_callstate_raw_info(callstate_raw_info, callee)

                if caller in caller_map:
                    # Caller already exists in our relations
                    relation_idx = caller_map[caller]
                    relation = relations[relation_idx]

                    # Check if callstate already exists
                    if not callstate in relation.callees:
                        relation.callees.append(callstate)
                else:
                    # New caller
                    relations.append(Relation(caller=caller, callees=[callstate]))
                    caller_map[caller] = len(relations) - 1

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

                thread_id_str, callstate_raw_info, callee_raw_info = line.split(",")
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
                    thread_id_str, callstate_raw_info, callee_raw_info = line.split(",")
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
                thread_id_str, callstate_raw_info, callee_raw_info = line.split(",")

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

    def trace(self, harness: str, input_data: bytes):
        harness_path = os.path.join(self.workdir, harness)

        temp_dir = self.trace_output_dir.name
        temp_input_path = os.path.join(temp_dir, "input")
        temp_trace_output_path = self.trace_output_path

        with open(temp_input_path, "wb") as f:
            f.write(input_data)

        cmd = [
            harness_path,
            "-runs=1",
            f"--trace_dump={temp_trace_output_path}",
            temp_input_path,
        ]
        try:
            subprocess.run(
                cmd,
                # capture_output=True,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                timeout=1500,
            )
        except:
            pass
        return

    def cleanup(self):
        self.trace_output_dir.cleanup()
