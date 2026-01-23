import asyncio
import importlib
import time
from collections import defaultdict
from pathlib import Path
from queue import Queue
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from mlla.agents.bugcandidate_agent.path_extractor import path_consumer
from mlla.utils.context import GlobalContext


@pytest.fixture
def dummy_gc(monkeypatch):
    monkeypatch.setattr(GlobalContext, "__init__", lambda self, *args, **kwargs: None)
    gc = GlobalContext(no_llm=True, cp_path=Path("dummy"))
    return gc


class DummyState(TypedDict):
    state: str


class DummyGraph:
    def __init__(self, worker_id: int, shared_counts: dict):
        self.worker_id = worker_id
        self.shared_counts = shared_counts

        self.builder = StateGraph(DummyState)
        self.builder.add_node("dummy", self.dummy_ainvoke)
        self.builder.add_edge(START, "dummy")
        self.builder.add_edge("dummy", END)

    async def dummy_ainvoke(self, state):
        self.shared_counts[self.worker_id] += 1
        time.sleep(0.1)
        return {}

    def compile(self):
        return self.builder.compile()


@pytest.mark.asyncio
async def test_path_consumer_single_state(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return ["path1"]

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    queue.put({"state": "state"})
    queue.put(None)

    num_workers = 1
    counts = defaultdict(int)
    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()

    assert queue.qsize() == 0
    assert counts[0] == 1


@pytest.mark.asyncio
async def test_path_consumer_extraction_fails(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        raise Exception("test error")

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    queue.put({"state": "state"})
    queue.put(None)

    num_workers = 1
    counts = defaultdict(int)
    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()
    assert queue.qsize() == 0
    assert counts[0] == 0, "No tasks should be processed"


@pytest.mark.asyncio
async def test_path_consumer_multiple_states(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return ["path1"]

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    counts = defaultdict(int)
    for i in range(10):
        queue.put({"state": f"state{i}"})
    queue.put(None)

    num_workers = 1
    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()
    assert queue.qsize() == 0
    assert counts[0] == 10


@pytest.mark.asyncio
async def test_path_consumer_multiple_workers(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return ["path1"]

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    num_workers = 2
    counts = defaultdict(int)

    for i in range(10):
        queue.put({"state": f"state{i}"})
    for _ in range(num_workers):
        queue.put(None)

    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()
    assert queue.qsize() == 0
    assert counts[0] > 0
    assert counts[1] > 0
    assert counts[0] + counts[1] == 10


@pytest.mark.asyncio
async def test_path_consumer_handles_errors(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return ["path1"]

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    num_workers = 2
    for i in range(10):
        queue.put({"state": f"state{i}"})
    for _ in range(num_workers):
        queue.put(None)

    counts = defaultdict(int)
    error_graph = DummyGraph(0, counts)

    def error_ainvoke(self, state):
        time.sleep(0.1)
        self.shared_counts[self.worker_id] += 1
        raise Exception("test error")

    monkeypatch.setattr(error_graph, "dummy_ainvoke", error_ainvoke)
    normal_graph = DummyGraph(1, counts)

    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, error_graph.compile(), worker_id=0)
        ),
        asyncio.create_task(
            path_consumer(dummy_gc, queue, normal_graph.compile(), worker_id=1)
        ),
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    assert queue.qsize() == 0
    assert counts[0] > 0, "Some tasks failed"
    assert counts[1] > 0, "Some tasks succeeded"
    assert counts[0] + counts[1] == 10, "All tasks should be processed"


@pytest.mark.asyncio
async def test_path_consumer_multiple_paths(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return ["path1", "path2"]

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    num_workers = 2
    counts = defaultdict(int)
    for i in range(10):
        queue.put({"state": f"state{i}"})
    for _ in range(num_workers):
        queue.put(None)

    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()
    assert queue.qsize() == 0
    assert counts[0] > 0
    assert counts[1] > 0
    assert counts[0] + counts[1] == 20, "All paths should be processed"


@pytest.mark.asyncio
async def test_path_consumer_no_path(dummy_gc, monkeypatch):
    queue = Queue()

    def fake_extract(gc, state):
        return []

    consumer_mod = importlib.import_module(path_consumer.__module__)
    monkeypatch.setattr(consumer_mod, "extract_unexplored_paths", fake_extract)

    num_workers = 2
    counts = defaultdict(int)
    for i in range(10):
        queue.put({"state": f"state{i}"})
    for _ in range(num_workers):
        queue.put(None)

    consumer_tasks = [
        asyncio.create_task(
            path_consumer(dummy_gc, queue, DummyGraph(i, counts).compile(), worker_id=i)
        )
        for i in range(num_workers)
    ]

    for task in consumer_tasks:
        try:
            await task
        except Exception as e:
            assert False, f"Task failed: {e}"

    queue.join()
    assert queue.qsize() == 0
    assert counts[0] == 0
    assert counts[1] == 0
    assert counts[0] + counts[1] == 0, "No tasks should be processed"
