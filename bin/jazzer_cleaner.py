#!/usr/bin/env python3

import os
import time
import glob
import subprocess
from pathlib import Path

INTERVAL = 300

PATTERNS = [
    "/tmp/byteBuddyAgent*.jar",
    "/tmp/rules_jni.*",
    "/tmp/jazzer-agent-*.jar",
]


def remove(path):
    subprocess.run(["rm", "-rf", path], check=False)


def clean_pids():
    PREFIX = "/tmp/.java_pid"
    pids = glob.glob(PREFIX + "*")
    lives = []
    for fname in pids:
        try:
            pid = fname[len(PREFIX) :]
            if os.path.exists(f"/proc/{pid}"):
                lives.append(pid)
            else:
                remove(fname)
        except:
            continue
    return lives


def list_owned_file(pid):
    ret = set()
    base = Path(f"/proc/{pid}/fd")
    for fd in base.iterdir():
        try:
            ret.add(str(fd.resolve()))
        except:
            continue
    try:
        with open(f"/proc/{pid}/maps", "rt") as f:
            for line in f.read().split("\n"):
                if "/" in line:
                    fname = line[line.find("/") :].strip()
                    ret.add(fname)
                    if fname.startswith("/tmp/rules_jni."):
                        ret.add(fname[: fname.rfind("/")])
    except:
        pass
    return ret


def clean():
    candidates = []
    for p in PATTERNS:
        candidates += glob.glob(p)
    live_pids = clean_pids()
    live_files = set()
    for pid in live_pids:
        live_files = live_files.union(list_owned_file(pid))
    for c in candidates:
        if c not in live_files:
            remove(c)


if __name__ == "__main__":
    while True:
        time.sleep(INTERVAL)
        clean()
