#!/usr/bin/env python3

import os
import sys
import time
import ctypes
import random
import importlib.util


class MsaMgr:
    def __init__(self):
        lib_path = os.getenv("MANAGER_LIB_PATH")
        harness_name = os.getenv("HARNESS_NAME")
        self.lib = ctypes.CDLL(lib_path)

        self.lib.init_mgr.restype = ctypes.c_void_p
        self.lib.init_mgr.argtypes = [ctypes.c_char_p, ctypes.c_bool]
        self.lib.alloc_input.restype = ctypes.c_int
        self.lib.alloc_input.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.get_input_buffer.restype = ctypes.c_void_p
        self.lib.get_input_buffer.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.set_input_metadata.restype = None
        self.lib.set_input_metadata.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulonglong,
        ]

        self.mgr_ptr = self.lib.init_mgr(
            ctypes.create_string_buffer(harness_name.encode()), ctypes.c_bool(False)
        )

        self.worker_idx = ctypes.c_int(int(os.getenv("CUR_WORKER")))
        self.max_input_size = int(os.getenv("MAX_INPUT_SIZE"))

    def write_blob(self, blob, seed_id=0xFFFFFFFFFFFFFFFF):
        if not isinstance(blob, bytes):
            return False
        idx = self.lib.alloc_input(self.mgr_ptr, self.worker_idx)
        if idx == -1:
            return False
        blob = blob[: self.max_input_size]
        buffer = self.lib.get_input_buffer(self.mgr_ptr, idx)
        ctypes.memmove(buffer, blob, len(blob))
        self.lib.set_input_metadata(self.mgr_ptr, idx, len(blob), seed_id)
        return True


def load_generate(script_path):
    try:
        spec = importlib.util.spec_from_file_location("script", script_path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        return script.generate
    except:
        return None


def loop(mgr, generate, num_blobs):
    random.seed(time.time_ns())
    for _ in range(num_blobs):
        blob = None
        try:
            blob = generate(random)
        except:
            pass
        if blob is None:
            continue
        if not mgr.write_blob(blob):
            break


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: run_mlla_gen.py <script_path> <num_blobs>")
        sys.exit(1)
    script_path = sys.argv[1]
    num_blobs = int(sys.argv[2])
    generate = load_generate(script_path)
    if generate is None:
        print(f"Failed to load generate function from {script_path}")
        sys.exit(1)
    mgr = MsaMgr()
    loop(mgr, generate, num_blobs)
