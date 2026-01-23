import asyncio
import traceback
from copy import deepcopy
from hashlib import sha256
from queue import Queue
from typing import Dict, List, Optional

from langgraph.graph.state import CompiledStateGraph
from loguru import logger
from pydantic import BaseModel, Field
from redis import Redis

from mlla.utils.cg import CG, FuncInfo, SinkDetectReport
from mlla.utils.context import GlobalContext


class ExtractedPath(BaseModel):
    paths_to_sink: List[FuncInfo]
    sink_line: str
    sanitizer_candidates: List[str]
    sink_detector_report: Optional[SinkDetectReport] = Field(default=None)

    def create_tag(self) -> str:
        query = "".join(
            [
                f"{f.func_location.func_name}:{f.func_location.file_path}:"
                f"{f.func_location.start_line}:{f.func_location.end_line}"
                for f in self.paths_to_sink
            ]
        )
        if self.sink_detector_report:
            query += (
                f"{self.sink_line}:{self.sanitizer_candidates}:"
                f"{self.sink_detector_report.sink_analysis_message}"
            )
        else:
            query += f"{self.sink_line}:{self.sanitizer_candidates}"

        return sha256(query.encode()).hexdigest()


REDIS_WIP_KEY = "extracted_paths::wip"
REDIS_DONE_KEY = "extracted_paths::done"


def reset_incompletes(gc: GlobalContext):
    redis: Redis = gc.redis
    redis.delete(REDIS_WIP_KEY)


def set_done(gc: GlobalContext, paths: List[ExtractedPath]):
    redis: Redis = gc.redis
    for path in paths:
        redis.sadd(REDIS_DONE_KEY, path.create_tag())
    if len(paths) != 1:
        logger.info(f"[PATH_EXTRACTOR] BCDA runs with {len(paths)} paths")
        for path in paths:
            logger.info(
                "[PATH_EXTRACTOR] path:"
                f" {[node.func_location.func_name for node in path.paths_to_sink]}"
            )


def is_unique_sink(gc: GlobalContext, path: ExtractedPath) -> bool:
    """Check if the path is unique/unexplored before"""
    redis: Redis = gc.redis

    path_id = f"{gc.cur_harness.name}:{path.create_tag()}"
    logger.info(f"[PATH_EXTRACTOR] REDIS check path_id: {path_id}")
    if redis.sismember(REDIS_DONE_KEY, path_id):
        # already in done
        logger.debug("[PATH_EXTRACTOR] Already in done")
        return False

    added = redis.sadd(REDIS_WIP_KEY, path_id)
    if added:
        # newly added to wip
        logger.debug("[PATH_EXTRACTOR] Newly added to wip")
        return True
    else:
        # already in wip
        logger.info("[PATH_EXTRACTOR] Already in wip")
        return False


def extract_path_recursive(
    gc: GlobalContext,
    node: FuncInfo,
    paths_to: List[FuncInfo],
) -> List[ExtractedPath]:
    """Recursively extract paths with vulnerabilities"""
    results: List[ExtractedPath] = []

    for child in node.children:
        results.extend(extract_path_recursive(gc, child, paths_to + [node]))

    report = node.sink_detector_report
    if report and report.is_vulnerable and report.sink_line:
        path = ExtractedPath(
            paths_to_sink=paths_to + [node],
            sink_line=report.sink_line,
            sanitizer_candidates=report.sanitizer_candidates,
            sink_detector_report=report,
        )
        if is_unique_sink(gc, path):
            results.append(path)
            # print(path)
            # print(results)
    return results


def extract_unexplored_paths(gc: GlobalContext, state):
    """Extract vulnerable paths from the call graph"""

    all_cgs: Dict[str, List[CG]] = state["CGs"]
    unexplored_paths: List[ExtractedPath] = []

    for harness_name, harness_cgs in all_cgs.items():
        logger.info(
            f"[PATH_EXTRACTOR] Processing {harness_name} with {len(harness_cgs)} CGs"
        )

        for cg in harness_cgs:
            unexplored_paths.extend(extract_path_recursive(gc, cg.root_node, []))

        logger.info(
            f"[PATH_EXTRACTOR] Extracted {len(unexplored_paths)} new potentially"
            f" vulnerable paths for {harness_name}"
        )

    return unexplored_paths


async def path_consumer(
    gc: GlobalContext,
    queue: Queue,
    bcda_bga_graph: CompiledStateGraph,
    worker_id: int,
):
    """Consume paths from the queue"""
    logger.info(f"[Consumer-{worker_id}] Running...")

    while True:
        state = await asyncio.to_thread(queue.get)
        logger.info(f"[Consumer-{worker_id}] Received state")
        if state is None:
            queue.task_done()
            logger.info(f"[Consumer-{worker_id}] Received sentinel. Terminating.")
            break

        try:
            unexplored_paths = extract_unexplored_paths(gc, state)
        except Exception as e:
            logger.error(f"[Consumer-{worker_id}] Extraction failed: {e}")
            queue.task_done()
            continue

        tasks = []
        for path in unexplored_paths:
            supernode_input = deepcopy(state)
            # This is for avoiding heavy copy of CGs
            # supernode_input = {}
            # for k, v in state.items():
            #     if k != "CGs":
            #         supernode_input[k] = deepcopy(v)
            #     else:
            #         supernode_input[k] = v
            supernode_input["extracted_paths"] = [path]
            task = asyncio.create_task(bcda_bga_graph.ainvoke(supernode_input))
            tasks.append(task)

        if len(tasks) > 1:
            logger.warning(
                f"[Consumer-{worker_id}] Running {len(tasks)} tasks in parallel"
            )
        else:
            logger.info(f"[Consumer-{worker_id}] Running {len(tasks)} task in parallel")

        for completed in asyncio.as_completed(tasks):
            try:
                result = await completed
                if result is not None:
                    logger.info(f"[Consumer-{worker_id}] BGA results: {result}")
            except Exception as e:
                logger.error(f"[Consumer-{worker_id}] Task failed: {e}")
                tb_lines = traceback.format_exception(
                    type(e), e, e.__traceback__, chain=True
                )
                logger.error("".join(tb_lines))
        logger.info(f"[Consumer-{worker_id}] Task done")
        queue.task_done()
