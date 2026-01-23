import argparse
import asyncio
import json
import os
import sys
import time
from enum import Enum
from pathlib import Path

from agents.reverser import ReverserAgent
from filelock import FileLock
from libCRS.otel import install_otel_logger
from loguru import logger
from tools.context import ReverserContext

import testlang


class ConfigKey(Enum):
    TARGET = "harness_src_path"
    NAME = "harness_name"
    BASEDIR = "project_src_dir"
    DIFF = "diff_path"
    POV_DIR = "pov_dir"
    MAX_BYTES_SIZE = "max_len"


DEFAULT_MODEL = "claude-3-7-sonnet-20250219"


async def main():
    parser = argparse.ArgumentParser(description="Reverser Agent")
    parser.add_argument(
        "--config-path", type=str, required=True, help="Path to the config file"
    )
    parser.add_argument("--workdir", type=str, required=True, help="Working directory")
    parser.add_argument(
        "--codegendir",
        type=str,
        required=True,
        help="Output directory for python codes",
    )
    parser.add_argument(
        "--majority",
        type=int,
        choices=range(1, 21),
        default=2,
        help="Number of majority votes (1-20)",
    )
    parser.add_argument("--debug", action="store_true", help="Print debug information")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Model to use")
    parser.add_argument("--outputs", type=str, help="Output directory for the testlang")
    parser.add_argument("--lock", type=str, help="Lock to output directory")
    parser.add_argument(
        "--corpus-map", type=str, help="Path to the testlang_input_gen corpus map file"
    )
    parser.add_argument(
        "--used-testlangs",
        type=str,
        help="Path to the used testlangs id set"
    )
    parser.add_argument(
        "--used-testlangs-timeout",
        type=int,
        help="Timeout for waiting for used testlangs (in seconds)",
        default=600,
    )
    parser.add_argument(
        "--deprioritized-testlangs",
        type=str,
        help="Path to the deprioritized testlangs id set (not used in this version)",
    )
    parser.add_argument("--visualize-output", action="store_true", help="Visualize output")
    parser.add_argument("--graph", action="store_true", help="Save graph as PNG")
    parser.add_argument("--log", action="store_true", help="Verbose logging")
    # Add telemetry flags
    parser.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Enable telemetry for LLM operations",
    )
    parser.add_argument(
        "--telemetry-endpoint",
        type=str,
        default=None,
        help="OpenTelemetry collector endpoint",
    )
    parser.add_argument(
        "--project-name",
        type=str,
        default="default",
        help="Project name for telemetry",
    )
    args = parser.parse_args()

    args.config_path = Path(args.config_path).resolve()
    args.workdir = Path(args.workdir).resolve()
    args.codegendir = Path(args.codegendir).resolve()
    if args.outputs:
        args.outputs = Path(args.outputs).resolve()
        os.makedirs(args.outputs, exist_ok=True)
    if args.lock:
        args.lock = Path(args.lock).resolve()
    if args.corpus_map:
        args.corpus_map = Path(args.corpus_map).resolve()
    if args.used_testlangs:
        args.used_testlangs = Path(args.used_testlangs).resolve()
    if args.deprioritized_testlangs:
        args.deprioritized_testlangs = Path(args.deprioritized_testlangs).resolve()

    basedir = os.getenv("CP_SRC")
    if basedir is None:
        basedir = "/src/repo"
    diff = None
    pov_dir = None

    with open(args.config_path, "r") as config_file:
        config = json.load(config_file)
        target = Path(config[ConfigKey.TARGET.value]).resolve()
        harness_name = config[ConfigKey.NAME.value]
        max_bytes_size = int(config[ConfigKey.MAX_BYTES_SIZE.value])

        if ConfigKey.BASEDIR.value in config:
            basedir = config[ConfigKey.BASEDIR.value]
        if ConfigKey.DIFF.value in config:
            diff = Path(config[ConfigKey.DIFF.value]).resolve()
        if ConfigKey.POV_DIR.value in config:
            pov_dir = Path(config[ConfigKey.POV_DIR.value]).resolve()

    basedir = Path(basedir).resolve()

    answer_testlang = os.environ.get("ANSWER_TESTLANG")
    if answer_testlang is not None:
        answer_testlang = Path(answer_testlang)
        if answer_testlang.exists():
            with FileLock(args.lock):
                timestamp = time.time_ns()
                Path(args.outputs / f"{timestamp}.testlang").write_text(
                    answer_testlang.read_text()
                )

                answer_codegen_dir = os.environ.get("ANSWER_CODEGEN_DIR")
                if answer_codegen_dir is not None:
                    answer_codegen_dir = Path(answer_codegen_dir)
                    if answer_codegen_dir.exists():
                        for python_code in answer_codegen_dir.glob("*.py"):
                            codegen_dir = (
                                Path(args.workdir) / "processors" / str(timestamp)
                            )
                            os.makedirs(codegen_dir, exist_ok=True)
                            codegen_path = codegen_dir / f"{python_code.name}.py"
                            codegen_path.write_text(python_code.read_text())

            return

    if args.workdir:
        os.chdir(args.workdir)

    # Setup telemetry before initializing other components
    if args.enable_telemetry:
        from mlla.utils.telemetry import setup_telemetry

        setup_telemetry(
            project_name=args.project_name,
            endpoint=args.telemetry_endpoint,
        )

    logger.remove()
    if args.log:
        # enable verbose logging in debug mode
        logger.add(sys.stderr, level="DEBUG", colorize=True)
    else:
        logger.add(sys.stderr, level="INFO", colorize=True)
    install_otel_logger(action_name=f"reverser-{harness_name}")

    # Add file handler for logging to log.txt in workdir
    if args.workdir:
        log_file = args.workdir / "log.txt"
        logger.add(log_file, level="DEBUG")

    gc = ReverserContext(
        args.config_path,
        args.workdir,
        args.codegendir,
        target,
        args.outputs,
        args.lock,
        max_bytes_size,
        basedir,
        corpus_map=args.corpus_map,
        used_testlangs=args.used_testlangs,
        used_testlangs_timeout=args.used_testlangs_timeout,
        deprioritized_testlangs=args.deprioritized_testlangs,
        pov_dir=pov_dir,
        visualize_output=args.visualize_output,
    )
    agent = ReverserAgent(gc, majority=args.majority, model=args.model)
    graph = agent.compile()
    if args.graph:
        png = graph.get_graph(xray=2).draw_mermaid_png()
        with open("reverser.png", "wb") as f:
            f.write(png)
        return

    final_state = await graph.ainvoke(
        {
            "harness_path": target,
            "harness_name": harness_name,
            "diff_path": diff,
        },
        {
            "recursion_limit": 200,
        },
        debug=args.debug,
    )
    # logger.debug(final_state)
    if not final_state["testlang"]:
        raise Exception("No testlang generated")
    logger.info(f"Generated testlang for {target}:\n {final_state['testlang']}")

    logger.info(f"Generated {len(final_state['python_codes'])} Python codes:")
    for python_code in final_state["python_codes"].values():
        if python_code:
            logger.info(f" - {python_code.name}")
            logger.info(python_code.code)

if __name__ == "__main__":
    asyncio.run(main())
