from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from app.shared.infrastructure.models import RunVerdict


class WorkerCompleteRequest(BaseModel):
    verdict: RunVerdict
    metrics: dict[str, Any] = Field(default_factory=dict)
    scenario_runner_exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class WorkerFailedRequest(BaseModel):
    error_code: str = Field(min_length=1, max_length=120)
    error_message: str | None = Field(default=None, max_length=4000)


class RunResultResponse(BaseModel):
    id: int
    run_job_id: int
    test_case_version_id: int
    verdict: RunVerdict
    metrics: dict[str, Any]
    scenario_runner_exit_code: int | None
    duration_ms: int | None


class RunResultListItem(RunResultResponse):
    test_case_id: int
    case_key: str
    title: str
    version_no: int
    map_code: str
    adversary_type: str
    environment_code: str
    collision: bool
    min_ttc_seconds: float | None
    created_at: datetime


class RunResultPage(BaseModel):
    items: list[RunResultListItem]
    page: int
    page_size: int
    total: int


class RunJobResponse(BaseModel):
    id: int
    suite_run_id: int | None
    test_case_version_id: int
    status: str
    worker_id: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    result: RunResultResponse | None
