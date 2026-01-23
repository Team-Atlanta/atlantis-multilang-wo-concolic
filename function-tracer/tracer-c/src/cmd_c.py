import os
import json
import shutil
import argparse

from loguru import logger
from tracer import DynamoRIOTracer


DYNAMORIO_HOME = os.getenv("DYNAMORIO_HOME")
DYNAMORIO_PLUGIN = os.getenv("DYNAMORIO_PLUGIN")


def parse_args():
    parser = argparse.ArgumentParser(description="C tracer")

    command_parsers = parser.add_subparsers(dest="command", help="command: trace")
    command_parsers.required = True

    trace_options = command_parsers.add_parser("trace", help="trace inputs")
    trace_options.add_argument("--harness", required=True, help="harness name")
    trace_options.add_argument("--seed", required=True, help="seed data path")
    trace_options.add_argument("--output", required=True, help="trace output path")
    trace_options.add_argument("--fuzzerdir", required=True, help="fuzzer directory")

    return parser.parse_args()


def trace_one(fuzzer_dir: str, harness_name: str, input_path: str, output_path: str):
    with open(input_path, "rb") as f:
        input_data = f.read()

    logger.info(f"Tracing: {harness_name} - {input_path}")
    tracer = DynamoRIOTracer(
        fuzzer_dir,
        copy=True,
        dynamorio_home_path=DYNAMORIO_HOME,
        dynamorio_plugin_path=DYNAMORIO_PLUGIN,
    )
    tracer.trace(harness_name, input_data)

    # traced_all = tracer.parse_raw_data_for_trace()

    logger.info(f"Parse and dump trace info: {harness_name} - {input_path}")

    # dumped_model = dict()
    # for thread_id, relations in traced_all.items():
    #     dumped_model[thread_id] = [relation.model_dump() for relation in relations]

    # with open(output_path, "w") as f:
    #     f.write(json.dumps(dumped_model))

    tracer.parse_raw_data_for_trace_direct_dump_jsonl(output_path)
    tracer.cleanup()
    logger.info(f"Trace done: {harness_name} - {input_path}")


if __name__ == "__main__":
    args = parse_args()

    match args.command:
        case "trace":

            trace_one(args.fuzzerdir, args.harness, args.seed, args.output)

        case _:
            print(f"Invalid command: {args.command}")
