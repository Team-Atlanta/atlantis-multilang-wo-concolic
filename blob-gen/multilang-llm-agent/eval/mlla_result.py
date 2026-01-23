from typing import Dict, List, Optional

from pydantic import BaseModel


class BlobStats(BaseModel):
    failed: int
    succeeded: int
    total: int


class HarnessStatusEntry(BaseModel):
    exploited: bool
    successful_blobs: int
    total_blobs: int


class LLMPerAgentEntry(BaseModel):
    completion_tokens: int
    execution_time: str
    model: Optional[str]
    prompt_tokens: int
    successful_requests: int
    temperature: float
    total_cost: float
    total_tokens: int


class LLMTotal(BaseModel):
    completion_tokens: int
    execution_time: float
    prompt_tokens: int
    successful_requests: int
    total_cost: float
    total_tokens: int


class LLMetrics(BaseModel):
    per_agent: Dict[str, LLMPerAgentEntry]
    total: LLMTotal


class SanitizerResultEntry(BaseModel):
    blob: str
    harness: str


class MLLAResult(BaseModel):
    blob_stats: BlobStats
    harness_status: Dict[str, HarnessStatusEntry]
    llm_metrics: LLMetrics
    sanitizer_results: Dict[str, List[SanitizerResultEntry]]
