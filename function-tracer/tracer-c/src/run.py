import threading
import os

from loguru import logger
from worker import worker_corpus_searcher, worker_tracer


def initialize_environment() -> None:
    trace_outputdir = os.getenv("CRS_SARIF_TRACER_TRACE_OUTPUTDIR")
    assert trace_outputdir is not None, "CRS_SARIF_TRACER_TRACE_OUTPUTDIR is not set"

    corpus_dir = os.getenv("CRS_SARIF_TRACER_CORPUS_DIRECTORY")
    assert corpus_dir is not None, "CRS_SARIF_TRACER_CORPUS_DIRECTORY is not set"

    os.makedirs(trace_outputdir, exist_ok=True)
    # os.makedirs(corpus_dir, exist_ok=True)


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

    create_and_start_workers()
