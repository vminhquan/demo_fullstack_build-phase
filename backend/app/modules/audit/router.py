from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    AuditLog,
    Project,
    ReviewRequest,
    RunJob,
    TestCase,
    TestCaseVersion,
    TestSuite,
    TestSuiteRun,
    User,
)

router = APIRouter(prefix="/audit-logs", tags=["audit"])

# Events that changed nothing and are no longer recorded; rows written before that are hidden.
NON_CHANGE_ACTIONS = ("PROJECT_SELECTED", "USER_LOGIN_SUCCEEDED")


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: int | None
    # Readable names so the audit screen does not show raw ids to non-technical admins.
    actor_name: str | None = None
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: int | None
    entity_label: str | None = None
    entity_version_id: int | None
    request_id: str | None
    before_data: dict | None
    after_data: dict | None
    created_at: datetime


async def _version_labels(session: AsyncSession, version_ids: set[int]) -> dict[int, str]:
    if not version_ids:
        return {}
    rows = await session.execute(
        select(TestCaseVersion.id, TestCaseVersion.version_no, TestCase.case_key, TestCase.title)
        .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
        .where(TestCaseVersion.id.in_(version_ids))
    )
    return {row.id: f"{row.case_key} · {row.title} (phiên bản {row.version_no})" for row in rows}


async def _entity_labels(session: AsyncSession, logs: list[AuditLog]) -> dict[tuple[str, int], str]:
    ids: dict[str, set[int]] = defaultdict(set)
    for log in logs:
        if log.entity_id is not None:
            ids[log.entity_type].add(log.entity_id)
    labels: dict[tuple[str, int], str] = {}

    if ids["USER"]:
        for row in await session.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(ids["USER"]))
        ):
            labels[("USER", row.id)] = row.display_name or row.email
    if ids["PROJECT"]:
        for row in await session.execute(
            select(Project.id, Project.name).where(Project.id.in_(ids["PROJECT"]))
        ):
            labels[("PROJECT", row.id)] = row.name
    if ids["TEST_CASE"]:
        for row in await session.execute(
            select(TestCase.id, TestCase.case_key, TestCase.title).where(TestCase.id.in_(ids["TEST_CASE"]))
        ):
            labels[("TEST_CASE", row.id)] = f"{row.case_key} · {row.title}"
    if ids["TEST_SUITE"]:
        for row in await session.execute(
            select(TestSuite.id, TestSuite.name).where(TestSuite.id.in_(ids["TEST_SUITE"]))
        ):
            labels[("TEST_SUITE", row.id)] = row.name
    if ids["TEST_SUITE_RUN"]:
        for row in await session.execute(
            select(TestSuiteRun.id, TestSuite.name)
            .join(TestSuite, TestSuite.id == TestSuiteRun.suite_id)
            .where(TestSuiteRun.id.in_(ids["TEST_SUITE_RUN"]))
        ):
            labels[("TEST_SUITE_RUN", row.id)] = f"Lượt chạy bộ “{row.name}”"

    # Reviews and run jobs are described through the test case version they concern.
    review_versions = {
        row.id: row.version_id
        for row in await session.execute(
            select(ReviewRequest.id, ReviewRequest.version_id).where(ReviewRequest.id.in_(ids["REVIEW_REQUEST"]))
        )
    } if ids["REVIEW_REQUEST"] else {}
    job_versions = {
        row.id: row.test_case_version_id
        for row in await session.execute(
            select(RunJob.id, RunJob.test_case_version_id).where(RunJob.id.in_(ids["RUN_JOB"]))
        )
    } if ids["RUN_JOB"] else {}
    versions = await _version_labels(
        session, ids["TEST_CASE_VERSION"] | set(review_versions.values()) | set(job_versions.values())
    )
    for version_id in ids["TEST_CASE_VERSION"]:
        if version_id in versions:
            labels[("TEST_CASE_VERSION", version_id)] = versions[version_id]
    for review_id, version_id in review_versions.items():
        if version_id in versions:
            labels[("REVIEW_REQUEST", review_id)] = versions[version_id]
    for job_id, version_id in job_versions.items():
        if version_id in versions:
            labels[("RUN_JOB", job_id)] = versions[version_id]
    return labels


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    action: str | None = None, actor_user_id: int | None = None, entity_type: str | None = None,
    entity_id: int | None = None, from_time: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
    actor: Principal = Depends(require("audit:read")), session: AsyncSession = Depends(get_session),
) -> list[AuditLogResponse]:
    statement = (
        select(AuditLog)
        .where(AuditLog.project_id == actor.project_id, AuditLog.action.not_in(NON_CHANGE_ACTIONS))
        .order_by(AuditLog.created_at.desc())
    )
    for column, value in ((AuditLog.action, action), (AuditLog.actor_user_id, actor_user_id), (AuditLog.entity_type, entity_type), (AuditLog.entity_id, entity_id)):
        if value is not None: statement = statement.where(column == value)
    if from_time is not None: statement = statement.where(AuditLog.created_at >= from_time)
    if to is not None: statement = statement.where(AuditLog.created_at <= to)
    statement = statement.offset((page - 1) * page_size).limit(page_size)
    logs = list((await session.scalars(statement)).all())

    actor_ids = {log.actor_user_id for log in logs if log.actor_user_id is not None}
    actors = {
        row.id: row
        for row in await session.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(actor_ids))
        )
    } if actor_ids else {}
    entities = await _entity_labels(session, logs)

    responses = []
    for log in logs:
        item = AuditLogResponse.model_validate(log, from_attributes=True)
        person = actors.get(log.actor_user_id) if log.actor_user_id is not None else None
        if person is not None:
            item.actor_name = person.display_name or person.email
            item.actor_email = person.email
        if log.entity_id is not None:
            item.entity_label = entities.get((log.entity_type, log.entity_id))
        responses.append(item)
    return responses
