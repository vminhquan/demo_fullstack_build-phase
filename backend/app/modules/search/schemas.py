from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from app.shared.infrastructure.models import DangerLevel, RunVerdict, VersionStatus


class RagSearchFilters(BaseModel):
    map_code: list[str] = Field(default_factory=list)
    adversary_type: list[str] = Field(default_factory=list)
    environment_code: list[str] = Field(default_factory=list)
    danger_level: list[DangerLevel] = Field(default_factory=list)
    status: list[VersionStatus] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)
    verdict: list[RunVerdict] = Field(default_factory=list)
    collision_only: bool = False


class RagSearchRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=1000)
    filters: RagSearchFilters = Field(default_factory=RagSearchFilters)
    limit: int = Field(default=12, ge=1, le=50)


class SimulationSummary(BaseModel):
    run_result_id: int | None = None
    run_job_id: int | None = None
    verdict: RunVerdict
    collision: bool
    min_ttc_seconds: float | None = None
    duration_ms: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class RagSearchHit(BaseModel):
    case_id: int
    case_key: str
    version_id: int
    version_no: int
    title: str
    status: VersionStatus
    map_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    tags: list[str]
    score: float
    matched_by: list[str]
    match_reasons: list[str]
    simulation: SimulationSummary | None = None


class RagSearchResponse(BaseModel):
    prompt: str
    interpreted_filters: RagSearchFilters
    items: list[RagSearchHit]
    total: int
    retrieval_mode: str
    semantic_fallback: bool
