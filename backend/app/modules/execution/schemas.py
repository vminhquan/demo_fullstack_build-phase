from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel

from app.shared.infrastructure.models import RunVerdict


class RunResultResponse(BaseModel):
    id: int
    run_job_id: int
    test_case_id: int
    revision: int | None = None
    verdict: RunVerdict
    metrics: dict[str, Any]
    scenario_runner_exit_code: int | None
    duration_ms: int | None


class RunResultListItem(RunResultResponse):
    case_key: str
    title: str
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
    test_case_id: int
    status: str
    worker_id: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    result: RunResultResponse | None
