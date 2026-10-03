from datetime import datetime
from pydantic import BaseModel, Field

from app.shared.infrastructure.models import DangerLevel, ReviewDecision, RunVerdict, VersionStatus


class SubmitReviewRequest(BaseModel):
    message: str | None = Field(default=None, max_length=4000)


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class DecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=4000)


class ReviewCommentResponse(BaseModel):
    id: int
    creator_id: int
    body: str
    comment_type: str
    created_at: datetime


class ReviewEvidence(BaseModel):
    """Latest simulation result recorded for the reviewed version, if any."""

    run_result_id: int
    run_job_id: int
    verdict: RunVerdict
    collision: bool
    min_ttc_seconds: float | None
    recorded_at: datetime
    total_runs: int


class ReviewResponse(BaseModel):
    id: int
    version_id: int
    requested_by: int
    requested_at: datetime
    resolved_by: int | None
    resolved_at: datetime | None
    decision: ReviewDecision | None
    comments: list[ReviewCommentResponse]
    # Context so review screens need no extra request per row.
    case_id: int | None = None
    case_key: str | None = None
    title: str | None = None
    version_no: int | None = None
    version_status: VersionStatus | None = None
    map_code: str | None = None
    adversary_type: str | None = None
    environment_code: str | None = None
    danger_level: DangerLevel | None = None
    requested_by_name: str | None = None
    resolved_by_name: str | None = None
    decision_comment: str | None = None
    evidence: ReviewEvidence | None = None

