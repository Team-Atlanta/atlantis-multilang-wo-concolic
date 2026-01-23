import asyncio
import os
import tempfile
from pathlib import Path

from loguru import logger

from ..context import GlobalContext
from ..coverage import InterestingSeedPolicy, InterestingSeedPolicyContext, init_fuzzdb
from .model import FunctionInfo, MethodInfo, TracerResult
from .parser import TracerParser


async def prepare_tracer(language: str, fuzzerdir: Path) -> bool:
    if language == "jvm":
        cmd = [
            "python3",
            "/tracer/java/cmd_java.py",
            "prepare",
            f"--fuzzerdir={fuzzerdir}",
        ]
        res = await asyncio.create_subprocess_exec(
            *cmd,
        )
        await res.wait()
        return res.returncode == 0
    else:
        return True


async def trace_pov(gc: GlobalContext, pov_path: Path) -> TracerResult:
    """Trace a POV and return the tracer result."""
    language = gc.cp.language

    fuzzerdir = Path("/out")
    if gc.cp.built_path:
        fuzzerdir = gc.cp.built_path

    with tempfile.NamedTemporaryFile() as f:
        output_path = Path(f.name)

        if language == "jvm":
            tracer_workdir = os.getenv("TRACER_WORKDIR", "/tracer_workdir")
            if not Path(tracer_workdir).exists():
                prepared = await prepare_tracer(language, fuzzerdir)
                if not prepared:
                    raise Exception("Failed to prepare tracer")

            tracer_java_path = os.getenv("TRACER_JAVA_PATH", "/tracer/java")

            cmd = [
                "python3",
                tracer_java_path + "/cmd_java.py",
                "trace",
                f"--harness={gc.cur_harness.name}",
                f"--seed={pov_path.as_posix()}",
                f"--output={output_path.as_posix()}",
            ]
        else:
            tracer_c_path = os.getenv("TRACER_C_PATH", "/tracer/c")
            cmd = [
                "python3",
                tracer_c_path + "/cmd_c.py",
                "trace",
                f"--fuzzerdir={fuzzerdir}",
                f"--harness={gc.cur_harness.name}",
                f"--seed={pov_path.as_posix()}",
                f"--output={output_path.as_posix()}",
            ]
        logger.info(f"Trace command: {' '.join(cmd)}")
        res = await asyncio.create_subprocess_exec(
            *cmd,
        )
        await res.wait()
        if res.returncode != 0:
            raise Exception("Failed to trace POV")

        with open(output_path, "r") as json_file:
            json_list = list(json_file)

        parser = TracerParser(json_list)
        relations = parser.parse()

        return TracerResult(relations, gc.cp.list_files_recursive())


async def _main():

    from ..coverage import load_interesting_seed

    cp_path = Path("/src")
    gc = GlobalContext(no_llm=False, cp_path=cp_path)

    fuzzdb = await asyncio.wait_for(init_fuzzdb(gc.cur_harness.name), timeout=300)
    policy_ctx = InterestingSeedPolicyContext(InterestingSeedPolicy.FUNCTION_COUNT)
    res = load_interesting_seed(fuzzdb, policy_ctx)

    if res is None:
        logger.warning("No interesting seed found")
        return

    seed_path, cov = res

    logger.info(f"Seed path: {seed_path}")
    # logger.info(f"Cov: {cov}")

    tracer_result = await asyncio.wait_for(trace_pov(gc, seed_path), timeout=300)
    # logger.info(f"Tracer result: {tracer_result}")

    list_files = gc.cp.list_files_recursive()
    relations = tracer_result.relations.filter_only_in_project(list_files)
    # relations = tracer_result.relations
    call_graph = tracer_result.call_graph

    logger.info(f"Relations: {relations}")

    Path("./relations.txt").write_text(str(relations))
    for caller, callees in call_graph.items():
        if isinstance(caller, FunctionInfo):
            logger.info(f"{caller.function_name} -> ")
        elif isinstance(caller, MethodInfo):
            logger.info(f"{caller.class_name}.{caller.method_name} -> ")
        for cs in callees:
            if isinstance(cs.callee, FunctionInfo):
                logger.info(f"\t{cs.callee.function_name}")
            elif isinstance(cs.callee, MethodInfo):
                logger.info(f"\t{cs.callee.class_name}.{cs.callee.method_name}")


if __name__ == "__main__":
    asyncio.run(_main())
