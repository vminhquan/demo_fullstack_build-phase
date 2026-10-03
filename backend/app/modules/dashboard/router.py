from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.shared.domain.policies import has_permission
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    ReviewRequest,
    RunResult,
    TestCase,
    TestCaseVersion,
    TestSuiteItem,
    VersionStatus,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class WorkSummary(BaseModel):
    # "all" for reviewers; "mine" means review/edit counts only cover the caller's own test cases.
    scope: str
    pending_review: int
    needs_changes: int
    approved_not_in_suite: int
    in_suite_without_result: int


@router.get("/summary", response_model=WorkSummary)
async def work_summary(
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> WorkSummary:
    own = None if has_permission(actor.permissions, "review:read") else actor.id

    pending = select(func.count()).select_from(ReviewRequest).where(
        ReviewRequest.project_id == actor.project_id, ReviewRequest.resolved_at.is_(None)
    )
    if own is not None:
        pending = pending.where(ReviewRequest.requested_by == own)

    # A case needs changes while its newest version is in EDIT (the creator must clone and resubmit).
    latest = (
        select(TestCaseVersion.test_case_id, func.max(TestCaseVersion.version_no).label("version_no"))
        .where(TestCaseVersion.project_id == actor.project_id)
        .group_by(TestCaseVersion.test_case_id)
        .subquery()
    )
    changes = (
        select(func.count())
        .select_from(TestCaseVersion)
        .join(latest, (latest.c.test_case_id == TestCaseVersion.test_case_id) & (latest.c.version_no == TestCaseVersion.version_no))
        .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
        .where(TestCaseVersion.status == VersionStatus.EDIT, TestCase.archived_at.is_(None))
    )
    if own is not None:
        changes = changes.where(TestCaseVersion.created_by == own)

    in_suite = exists().where(TestSuiteItem.test_case_version_id == TestCaseVersion.id)
    ready = (
        select(func.count())
        .select_from(TestCaseVersion)
        .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
        .where(
            TestCaseVersion.project_id == actor.project_id,
            TestCaseVersion.status == VersionStatus.APPROVED,
            TestCase.archived_at.is_(None),
            ~in_suite,
        )
    )
    has_result = exists().where(RunResult.test_case_version_id == TestSuiteItem.test_case_version_id)
    waiting = select(func.count(func.distinct(TestSuiteItem.test_case_version_id))).where(
        TestSuiteItem.project_id == actor.project_id, ~has_result
    )

    return WorkSummary(
        scope="mine" if own is not None else "all",
        pending_review=int(await session.scalar(pending) or 0),
        needs_changes=int(await session.scalar(changes) or 0),
        approved_not_in_suite=int(await session.scalar(ready) or 0),
        in_suite_without_result=int(await session.scalar(waiting) or 0),
    )
