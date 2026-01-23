import os
import json
import shutil
import argparse

from loguru import logger
from tracer import JazzerTracer

jazzer_api_path = os.getenv("TRACER_JAZZER_API_PATH")
jazzer_junit_path = os.getenv("TRACER_JAZZER_JUNIT_PATH")
jazzer_driver_path = os.getenv("TRACER_JAZZER_DRIVER_PATH")
jazzer_agent_path = os.getenv("TRACER_JAZZER_AGENT_PATH")

TRACER_WORKDIR = os.getenv("TRACER_WORKDIR")

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

def parse_args():
    parser = argparse.ArgumentParser(description="JAVA tracer")

    command_parsers = parser.add_subparsers(
        dest="command", help="command: [prepare | trace]"
    )
    command_parsers.required = True

    prepare_options = command_parsers.add_parser(
        "prepare", help="prepare work directory"
    )
    prepare_options.add_argument("--fuzzerdir", required=True, help="fuzzer directory")

    trace_options = command_parsers.add_parser("trace", help="trace inputs")
    trace_options.add_argument("--harness", required=True, help="harness name")
    trace_options.add_argument("--seed", required=True, help="seed data path")
    trace_options.add_argument("--output", required=True, help="trace output path")

    return parser.parse_args()


def prepare_tracer(workdir: str, target_dir: str) -> None:

    logger.info("Prepare workdir")
    if os.path.exists(workdir):
        shutil.rmtree(workdir)

    safe_copytree(target_dir, workdir)

    shutil.copy(jazzer_api_path, os.path.join(workdir, "jazzer_api_deploy.jar"))
    shutil.copy(jazzer_junit_path, os.path.join(workdir, "jazzer_junit.jar"))
    shutil.copy(jazzer_driver_path, os.path.join(workdir, "jazzer_driver"))
    shutil.copy(jazzer_agent_path, os.path.join(workdir, "jazzer_agent_deploy.jar"))

    logger.info(f"Workdir generated in {workdir}")


def trace_one(workdir: str, harness_name: str, input_path: str, output_path: str):
    with open(input_path, "rb") as f:
        input_data = f.read()

    logger.info(f"Tracing: {harness_name} - {input_path}")
    tracer = JazzerTracer(workdir)
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
        case "prepare":
            prepare_tracer(TRACER_WORKDIR, args.fuzzerdir)

        case "trace":
            if not os.path.exists(TRACER_WORKDIR):
                print(f"Please prepare tracer workdir: - {TRACER_WORKDIR}")
                exit(1)

            trace_one(TRACER_WORKDIR, args.harness, args.seed, args.output)

        case _:
            print(f"Invalid command: {args.command}")
