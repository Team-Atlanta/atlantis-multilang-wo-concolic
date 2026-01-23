#!/usr/bin/env python3

import sys
import json
from libCRS import (
    Config,
    init_cp_in_runner,
)


def run_povs(harness):
    povs = harness.get_answer_povs()
    logs = []
    for pov in povs:
        logs.append(harness.run_input(pov)[-1].decode("utf-8"))
    return logs


def handle_no_fdp(conf, cp):
    if "no_FDP" in conf.others:
        if conf.target_harnesses:
            targets = conf.target_harnesses
        else:
            targets = cp.get_harnesses().keys()
        targets = list(filter(lambda x: not x.endswith("FDP"), targets))
        conf.target_harnesses = targets


if __name__ == "__main__":
    conf = Config(0, 1).load("/crs.config")
    cp = init_cp_in_runner()
    handle_no_fdp(conf, cp)
    logs = {}
    for name, harness in cp.get_harnesses().items():
        if conf.is_target_harness(harness):
            logs[name] = run_povs(harness)
    sys.stdout.write(json.dumps(logs))
