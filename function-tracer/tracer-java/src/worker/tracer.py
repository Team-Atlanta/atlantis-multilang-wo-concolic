import queue
import threading
import os
import traceback
import json
import shutil

from loguru import logger

from corpus import Corpus
from .resources import corpus_queue
from tracer import JazzerTracer
from tracer.model import Relations


def worker_tracer(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            corpus: Corpus = corpus_queue.get(timeout=0.5)
            process_corpus(corpus)
            corpus_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"Error in worker_tracer: {traceback.format_exc()}")


def process_corpus(corpus: Corpus) -> None:
    tracer_workdir = os.getenv("CRS_SARIF_TRACER_TRACER_WORKDIR")

    logger.info(f"Tracing: {corpus.harness} - {os.path.basename(corpus.path)}")
    tracer = JazzerTracer(tracer_workdir)
    tracer.trace(corpus.harness, corpus.data)

    logger.info("Traing done, parse and dump log")
    traced_edges = tracer.parse_raw_trace_data_for_edges()
    dump_trace_output(corpus, traced_edges, "edges")
    del traced_edges
    # traced_all = tracer.parse_raw_data_for_trace()
    # dump_trace_output(corpus, traced_all, "trace")
    # del traced_all
    # dump_stream_trace_output(corpus, tracer)

    logger.info(f"Dump trace info: {corpus.harness} - {corpus.path}")
    tracer.cleanup()


def dump_stream_trace_output(corpus: Corpus, tracer: JazzerTracer) -> None:
    trace_outputdir = os.path.abspath(os.getenv("CRS_SARIF_TRACER_TRACE_OUTPUTDIR"))

    harness_dir = os.path.join(trace_outputdir, corpus.harness)
    if not os.path.isdir(harness_dir):
        os.makedirs(harness_dir, exist_ok=True)

    trace_output_path = os.path.join(
        harness_dir, f"{os.path.basename(corpus.path)}.trace"
    )

    # tracer.parse_raw_data_for_trace_direct_dump(trace_output_path)
    tracer.parse_raw_data_for_trace_direct_dump_jsonl(trace_output_path)


def dump_trace_output(
    corpus: Corpus, traced_funcs: dict[int, Relations], suffix: str
) -> None:
    trace_outputdir = os.path.abspath(os.getenv("CRS_SARIF_TRACER_TRACE_OUTPUTDIR"))

    harness_dir = os.path.join(trace_outputdir, corpus.harness)
    if not os.path.isdir(harness_dir):
        os.makedirs(harness_dir, exist_ok=True)

    trace_output_path = os.path.join(
        harness_dir, f"{os.path.basename(corpus.path)}.{suffix}"
    )

    tmp_trace_output_path = trace_output_path + ".tracing"

    if isinstance(traced_funcs, dict):
        dumped_models = dict()
        for thread_id, relations in traced_funcs.items():
            dumped_models[thread_id] = [relation.model_dump() for relation in relations]
        with open(tmp_trace_output_path, "w") as f:
            f.write(json.dumps(dumped_models))
        shutil.move(tmp_trace_output_path, trace_output_path)

    else:
        with open(tmp_trace_output_path, "w") as f:
            f.write(json.dumps([relation.model_dump() for relation in traced_funcs]))
        shutil.move(tmp_trace_output_path, trace_output_path)
