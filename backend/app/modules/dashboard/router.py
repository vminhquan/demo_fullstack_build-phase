from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.shared.domain.policies import has_permission
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import RunResult, TestCase, TestCaseStatus, TestSuiteItem

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class WorkSummary(BaseModel):
    # "all" for reviewers; "mine" means pending/rejected counts only cover the caller's own test cases.
    scope: str
    pending_review: int
    rejected: int
    approved_not_in_suite: int
    in_suite_without_result: int


@router.get("/summary", response_model=WorkSummary)
async def work_summary(
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> WorkSummary:
    own = None if has_permission(actor.permissions, "review:decide") else actor.id
    live = (TestCase.project_id == actor.project_id, TestCase.archived_at.is_(None))

    def count_status(value: TestCaseStatus):
        statement = select(func.count()).select_from(TestCase).where(*live, TestCase.status == value)
        return statement.where(TestCase.created_by == own) if own is not None else statement

    in_suite = exists().where(TestSuiteItem.test_case_id == TestCase.id)
    ready = select(func.count()).select_from(TestCase).where(*live, TestCase.status == TestCaseStatus.APPROVED, ~in_suite)
    has_result = exists().where(RunResult.test_case_id == TestSuiteItem.test_case_id)
    waiting = select(func.count(func.distinct(TestSuiteItem.test_case_id))).where(
        TestSuiteItem.project_id == actor.project_id, ~has_result
    )
    return WorkSummary(
        scope="mine" if own is not None else "all",
        pending_review=int(await session.scalar(count_status(TestCaseStatus.PENDING)) or 0),
        rejected=int(await session.scalar(count_status(TestCaseStatus.REJECTED)) or 0),
        approved_not_in_suite=int(await session.scalar(ready) or 0),
        in_suite_without_result=int(await session.scalar(waiting) or 0),
    )
