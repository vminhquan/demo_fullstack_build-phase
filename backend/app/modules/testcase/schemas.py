from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from app.shared.infrastructure.models import DangerLevel, VersionStatus


class TestCaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None


class TestCaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None


class VersionPayload(BaseModel):
    map_code: str = Field(min_length=1, max_length=120)
    ego_vehicle_code: str = Field(min_length=1, max_length=255)
    adversary_type: str = Field(min_length=1, max_length=120)
    environment_code: str = Field(min_length=1, max_length=120)
    danger_level: DangerLevel
    scenario_input: dict[str, Any] = Field(default_factory=dict)
    tag_names: list[str] = Field(default_factory=list)
    change_note: str | None = None


class VersionUpdate(VersionPayload):
    pass


class VersionResponse(BaseModel):
    id: int
    test_case_id: int
    version_no: int
    status: VersionStatus
    map_code: str
    ego_vehicle_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    scenario_input: dict[str, Any]
    xosc_artifact_id: int | None
    change_note: str | None
    created_by: int
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    decided_by: int | None
    tags: list[str]


class TestCaseResponse(BaseModel):
    id: int
    case_key: str
    title: str
    description: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    latest_version: VersionResponse | None


class PageResponse(BaseModel):
    items: list[TestCaseResponse]
    page: int
    page_size: int
    total: int


class UploadResponse(BaseModel):
    artifact_id: int
    sha256: str
    size_bytes: int


class SearchFilters(BaseModel):
    map_code: list[str] = Field(default_factory=list)
    adversary_type: list[str] = Field(default_factory=list)
    environment_code: list[str] = Field(default_factory=list)
    danger_level: list[DangerLevel] = Field(default_factory=list)
    status: list[VersionStatus] = Field(default_factory=list)
    creator_id: list[int] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    mode: str = Field(default="hybrid", pattern="^(filter|keyword|semantic|hybrid)$")
    filters: SearchFilters = Field(default_factory=SearchFilters)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
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
    score: float | None = None
    matched_by: list[str]


class SearchResponse(BaseModel):
    items: list[SearchHit]
    page: int
    page_size: int
    total: int
    semantic_fallback: bool = False
