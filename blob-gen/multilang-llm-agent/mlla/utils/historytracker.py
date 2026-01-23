import json
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot
from loguru import logger
from redis import Redis


class HistoryTracker:
    graph: CompiledStateGraph
    latest_snapshots: list[StateSnapshot]
    latest_thread_id: int
    cur_idx: int
    cur_graph_config: dict

    def __init__(
        self, graph_config: dict, graph: CompiledStateGraph, redis: Redis
    ) -> None:
        latest_config = self.get_latest_config(redis)
        history = list(graph.get_state_history(latest_config))
        latest = history[0]
        l_thread_id = latest.config["configurable"]["thread_id"]
        latest_snapshots = []

        def check_snapshot(snapshot: StateSnapshot):
            s_thread_id = snapshot.config["configurable"]["thread_id"]
            if s_thread_id == l_thread_id:
                if len(snapshot.tasks) >= 1 and snapshot.tasks[0].state:
                    subhistory = graph.get_state_history(snapshot.tasks[0].state)
                    for subsnapshot in subhistory:
                        logger.debug(subsnapshot)
                        check_snapshot(subsnapshot)
                        # if subsnapshot.next and subsnapshot.next[0] != "__start__":
                        #     latest_snapshots.append(subsnapshot)
                ###
                elif not snapshot.next or (
                    snapshot.next and snapshot.next[0] != "__start__"
                ):
                    latest_snapshots.append(snapshot)

        for snapshot in history:
            check_snapshot(snapshot)

        latest_snapshots.reverse()
        # First component is empty, so we pop it
        latest_snapshots.pop(0)

        self.graph = graph
        self.latest_snapshots = latest_snapshots
        self.latest_thread_id = l_thread_id
        self.cur_idx = 0
        self.cur_graph_config = graph_config

        for snapshot in latest_snapshots:
            logger.debug(f"Snapshot: {snapshot.next}")

        logger.info(
            f"History tracker initialized with {len(latest_snapshots)} snapshots."
        )

    def get_cur_snapshot(self):
        assert self.cur_idx < len(self.latest_snapshots)
        cur_snapshot = self.latest_snapshots[self.cur_idx]

        self.cur_idx = self.cur_idx + 1

        return cur_snapshot

    def get_cur_state(self):
        cur_snapshot = self.get_cur_snapshot()
        return cur_snapshot.values

    def get_latest_config(self, redis: Redis) -> dict:
        serialized_data: Any = redis.get("latest_graph_config")
        if serialized_data is None:
            raise RuntimeError("No latest graph config found in Redis.")
        return json.loads(serialized_data)
