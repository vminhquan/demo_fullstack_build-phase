from datetime import datetime
from pydantic import BaseModel, Field

from app.shared.infrastructure.models import RunJobStatus, SuiteRunStatus


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    description: str | None = None


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=250)
    description: str | None = None


class AddSuiteItem(BaseModel):
    test_case_version_id: int


class ReorderItems(BaseModel):
    version_ids: list[int]


class SuiteItemResponse(BaseModel):
    test_case_version_id: int
    position: int
    added_by: int
    added_at: datetime


class SuiteResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
    items: list[SuiteItemResponse]


class SuiteRunResponse(BaseModel):
    id: int
    suite_id: int
    status: SuiteRunStatus
    total_jobs: int
    completed_jobs: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunJobSummary(BaseModel):
    id: int
    test_case_version_id: int
    status: RunJobStatus
    attempt: int
    error_code: str | None
    error_message: str | None
