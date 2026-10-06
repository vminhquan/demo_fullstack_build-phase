from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.shared.infrastructure.models import DangerLevel, TestCaseDecisionValue, TestCaseStatus


class TestCaseContent(BaseModel):
    """Everything a person can set on a test case (hand-written cases; generated ones come from the Agent)."""

    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    map_code: str = Field(min_length=1, max_length=120)
    ego_vehicle_code: str = Field(min_length=1, max_length=255)
    adversary_type: str = Field(min_length=1, max_length=120)
    environment_code: str = Field(min_length=1, max_length=120)
    danger_level: DangerLevel
    scenario_input: dict[str, Any] = Field(default_factory=dict)
    tag_names: list[str] = Field(default_factory=list)


class TestCaseUpdate(BaseModel):
    """In-place edit; omitted fields keep their value. Any change sends the case back to PENDING."""

    # Revision the editor loaded; a different current revision means someone else saved first (409).
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    map_code: str | None = Field(default=None, min_length=1, max_length=120)
    ego_vehicle_code: str | None = Field(default=None, min_length=1, max_length=255)
    adversary_type: str | None = Field(default=None, min_length=1, max_length=120)
    environment_code: str | None = Field(default=None, min_length=1, max_length=120)
    danger_level: DangerLevel | None = None
    scenario_input: dict[str, Any] | None = None
    tag_names: list[str] | None = None


class TestCaseResponse(BaseModel):
    id: int
    case_key: str
    title: str
    description: str | None
    status: TestCaseStatus
    map_code: str
    ego_vehicle_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    scenario_input: dict[str, Any]
    xosc_artifact_id: int | None
    xosc_sha256: str | None = None
    catalog_snapshot_id: int | None = None
    revision: int
    config_sha256: str
    created_by: int
    created_by_name: str | None = None
    last_edited_by: int | None = None
    last_edited_by_name: str | None = None
    last_edited_at: datetime | None = None
    decided_by: int | None = None
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    discarded_at: datetime | None = None
    locked_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[str]
    # Test Case Builder session that generated the case, and its position in that session.
    builder_session_id: int | None = None
    builder_variant_no: int | None = None
    # What the caller may do with this case right now: edit, discard, restore, decide.
    can: list[str] = Field(default_factory=list)


class PageResponse(BaseModel):
    items: list[TestCaseResponse]
    page: int
    page_size: int
    total: int


class UploadResponse(BaseModel):
    artifact_id: int
    sha256: str
    size_bytes: int


class DecisionRequest(BaseModel):
    decision: TestCaseDecisionValue
    # Revision the reviewer looked at; deciding on a newer edit by mistake is refused (409).
    expected_revision: int | None = Field(default=None, ge=1)


class BatchDecisionRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    decision: TestCaseDecisionValue


class BatchDiscardRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class BatchItemResult(BaseModel):
    id: int
    status: TestCaseStatus | None = None
    error: str | None = None
    message: str | None = None


class BatchResponse(BaseModel):
    results: list[BatchItemResult]


class LockRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class LockResponse(BaseModel):
    locked: list[int]
    # Cases that are not APPROVED (e.g. edited back to PENDING) are left out of the run.
    skipped: list[BatchItemResult]


class DecisionResponse(BaseModel):
    id: int
    test_case_id: int
    case_key: str | None = None
    title: str | None = None
    decision: TestCaseDecisionValue
    revision: int
    config_sha256: str
    decided_by: int
    decided_by_name: str | None = None
    created_at: datetime
    undone_at: datetime | None = None


class SearchFilters(BaseModel):
    map_code: list[str] = Field(default_factory=list)
    adversary_type: list[str] = Field(default_factory=list)
    environment_code: list[str] = Field(default_factory=list)
    danger_level: list[DangerLevel] = Field(default_factory=list)
    status: list[TestCaseStatus] = Field(default_factory=list)
    creator_id: list[int] = Field(default_factory=list)
    tag: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    mode: Literal["filter", "keyword", "semantic", "hybrid"] = "hybrid"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
    case_id: int
    case_key: str
    revision: int
    title: str
    status: TestCaseStatus
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
