import os
from abc import ABC, abstractmethod
from typing import Optional

from loguru import logger
from redis import Redis

# from pydantic import BaseModel

# class LLMCacheEntry(BaseModel):
#     prompt: str
#     response: str

# def empty_cache_entry() -> LLMCacheEntry:
#     return LLMCacheEntry(prompt="", response="")


class LLMCache(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    def set(self, key: str, value: str):
        pass


class RedisHashCache(LLMCache):
    def __init__(self, redis_client: Redis, hash_key: str):
        self.redis = redis_client
        self.hash = hash_key

    def get(self, name: str) -> Optional[str]:
        val = self.redis.hget(self.hash, name)
        if val is None:
            return None
        elif isinstance(val, bytes):
            return val.decode("utf-8")
        else:
            logger.warning(f"Redis response is not a bytes: {val}")
            return None

    def set(self, name: str, data: str):
        self.redis.hset(self.hash, name, data)


class FileCache(LLMCache):
    def __init__(self, base_dir: str):
        os.makedirs(base_dir, exist_ok=True)
        self.base_dir = base_dir

    def get(self, name: str) -> Optional[str]:
        path = os.path.join(self.base_dir, f"{name}.txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        else:
            return None

    def set(self, name: str, data: str):
        path = os.path.join(self.base_dir, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)


class NoCache(LLMCache):
    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: str):
        pass


class LLMCacheFactory:
    @staticmethod
    def create(config: dict) -> LLMCache:
        kind = config.get("kind")
        if kind == "file":
            return FileCache(config["base_dir"])
        elif kind == "redis":
            return RedisHashCache(
                redis_client=config["redis_client"],
                hash_key=config["hash_key"],
            )
        elif kind == "none":
            return NoCache()
        else:
            logger.error(f"Unknown cache kind: {kind}")
            return NoCache()
