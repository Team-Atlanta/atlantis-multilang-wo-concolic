import os
from loguru import logger
import time
import traceback
import threading

from corpus import Corpus, CorpusSearcher
from .resources import corpus_queue


def worker_corpus_searcher(stop_event: threading.Event):
    corpus_directory = os.path.abspath(os.getenv("CRS_SARIF_TRACER_CORPUS_DIRECTORY"))
    corpus_searcher = CorpusSearcher(corpus_directory)

    while not stop_event.is_set():
        try:
            new_corpuses = corpus_searcher.search_new_corpus()
            for new_corpus in new_corpuses:
                corpus_queue.put(new_corpus)
            time.sleep(10)
        except Exception as e:
            logger.error(f"Error in worker_corpus_searcher: {traceback.print_exc()}")
