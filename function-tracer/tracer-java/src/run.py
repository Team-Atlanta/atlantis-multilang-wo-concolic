import os
import shutil
import sys
import json
import threading
import tarfile

from loguru import logger
from tracer import JazzerTracer
from tracer.model import Relations
from worker import worker_corpus_searcher, worker_tracer


def initialize_environment() -> None:
    trace_outputdir = os.getenv("CRS_SARIF_TRACER_TRACE_OUTPUTDIR")
    assert trace_outputdir is not None, "CRS_SARIF_TRACER_TRACE_OUTPUTDIR is not set"

    corpus_dir = os.getenv("CRS_SARIF_TRACER_CORPUS_DIRECTORY")
    assert corpus_dir is not None, "CRS_SARIF_TRACER_CORPUS_DIRECTORY is not set"

    os.makedirs(trace_outputdir, exist_ok=True)
    # os.makedirs(corpus_dir, exist_ok=True)


def prepare_tracer(workdir: str, target_dir: str) -> None:
    jazzer_api_path = os.getenv("CRS_SARIF_TRACER_JAZZER_API_PATH")
    jazzer_junit_path = os.getenv("CRS_SARIF_TRACER_JAZZER_JUNIT_PATH")
    jazzer_driver_path = os.getenv("CRS_SARIF_TRACER_JAZZER_DRIVER_PATH")
    jazzer_agent_path = os.getenv("CRS_SARIF_TRACER_JAZZER_AGENT_PATH")

    fuzzing_resource_dir = os.getenv("MULTILANG_BUILD_DIR")
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
        # os.makedirs(workdir, exist_ok=True)

    # shutil.copytree(target_dir, workdir)
    os.makedirs(workdir, exist_ok=True)
    with tarfile.open(
        os.path.join(fuzzing_resource_dir, "fuzzers.tar.gz"), "r:*"
    ) as tar:
        tar.extractall(path=workdir)

    shutil.copy(jazzer_api_path, os.path.join(workdir, "jazzer_api_deploy.jar"))
    shutil.copy(jazzer_junit_path, os.path.join(workdir, "jazzer_junit.jar"))
    shutil.copy(jazzer_driver_path, os.path.join(workdir, "jazzer_driver"))
    shutil.copy(jazzer_agent_path, os.path.join(workdir, "jazzer_agent_deploy.jar"))


def create_and_start_workers():
    MAX_TRACER_WORKER_NUM = 10

    stop_event = threading.Event()

    tracer_workers = []
    logger.info("Start corpus_worker")
    corpus_worker = threading.Thread(
        target=worker_corpus_searcher, args=(stop_event,), daemon=True
    )
    corpus_worker.start()

    for _ in range(MAX_TRACER_WORKER_NUM):
        logger.info("Start tracer worker")
        worker = threading.Thread(target=worker_tracer, args=(stop_event,), daemon=True)
        worker.start()
        tracer_workers.append(worker)

    try:
        corpus_worker.join()
        for worker in tracer_workers:
            worker.join()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, stopping workers")
        stop_event.set()
        corpus_worker.join(timeout=2.0)
        for worker in tracer_workers:
            worker.join(timeout=2.0)


if __name__ == "__main__":
    # NOTE: Needed shared direcory info:
    # CRS_SARIF_TRACER_TRACE_OUTPUTDIR, CRS_SARIF_TRACER_CORPUS_DIRECTORY
    # This runner must spawn for each CP

    initialize_environment()

    logger.info("Preparing tracer environment")
    tracer_workdir = os.getenv("CRS_SARIF_TRACER_TRACER_WORKDIR")
    target_dir = os.getenv("OUT")
    prepare_tracer(tracer_workdir, target_dir)

    create_and_start_workers()
