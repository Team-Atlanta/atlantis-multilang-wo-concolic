#!/usr/bin/env python3

import os
import subprocess

excludes = [
    "*.tar.gz",
    "/src/pkgs",
    "fuzztest",
    "honggfuzz",
    "libfuzzer",
    "aflplusplus",
    "llvmsymbol.diff",
]


def to_rel(path):
    if path.startswith("/src/"):
        return path[5:]
    return path


os.system("compile")
mount = os.getenv("MOUNT_SRC_PATH")
excludes += [mount]
cmd = ["rsync", "-a"]
for ex in excludes:
    ex = to_rel(ex)
    cmd += [f"--exclude={ex}"]
cmd += ["/src/", "/work/multilang_proj"]
subprocess.run(cmd, check=False)
subprocess.run(["rm", "-rf", f"/work/multilang_proj/{to_rel(mount)}"])
