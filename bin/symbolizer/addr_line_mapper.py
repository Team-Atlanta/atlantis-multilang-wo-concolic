import os
import pickle
from typing import Dict, List, Set
from urllib.parse import urlparse

from cfg_dataclasses import LineInfo, Node
from redis import Redis
from utils import is_running_under_pytest


class AddrLineMapper:
    def __init__(self, harness: str, redis_url: str) -> None:
        self.harness = harness
        self.redis_url = redis_url
        for file in [self.harness]:
            if not os.path.exists(file):
                raise FileNotFoundError(f"{file} not found")

        self.data = self.__load_data_from_redis()

    def __load_data_from_redis(self) -> Dict[int, Node]:
        parsed_url = urlparse(self.redis_url)
        redis_client = Redis(host=parsed_url.scheme, port=parsed_url.path)

        redis_key = f"{self.harness}"
        serialized_data = redis_client.get(redis_key)
        if serialized_data is None:
            return {}
        return pickle.loads(serialized_data)

    def translate(self, addrs: List[int]) -> Set[LineInfo]:
        line_infos: Set[LineInfo] = set()

        for addr in addrs:
            try:
                node = self.data[addr]
                line_infos.update(node.lines)

                if not node.fallback and not any(
                    addr in node.reachable_instrumented_addrs for addr in addrs
                ):
                    line_infos.update(
                        node.lines_from_addrs_reachable_wo_instrumentation
                    )
            except Exception as e:
                if is_running_under_pytest():
                    raise e
                continue

        return line_infos
