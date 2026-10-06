from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.builder.schemas import BuilderError, BuilderMapOptions, BuilderSessionCreate, BuilderSessionDetail, BuilderSessionSummary
from app.modules.builder.service import create_session, get_session_row, session_cases, start_run
from app.modules.generation.agent_client import AgentPort, get_agent
from app.modules.identity.dependencies import Principal, require
from app.modules.testcase.service import case_responses
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import BuilderSession, TestCase, TestCaseStatus, User

router = APIRouter(prefix="/builder-sessions", tags=["builder-sessions"])


async def creator_names(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    users = await session.scalars(select(User).where(User.id.in_(ids)))
    return {user.id: user.display_name or user.email for user in users.all()}


async def pending_counts(session: AsyncSession, project_id: int, builder_ids: list[int]) -> dict[int, int]:
    """PENDING cases per session (what reviewers still have to look at)."""
    if not builder_ids:
        return {}
    rows = await session.execute(
        select(TestCase.builder_session_id, func.count())
        .where(
            TestCase.project_id == project_id,
            TestCase.builder_session_id.in_(builder_ids),
            TestCase.status == TestCaseStatus.PENDING,
        )
        .group_by(TestCase.builder_session_id)
    )
    return {builder_id: int(count) for builder_id, count in rows}


def summary(builder: BuilderSession, names: dict[int, str] | None = None, pending: int = 0) -> BuilderSessionSummary:
    return BuilderSessionSummary(
        id=builder.id,
        title=builder.title,
        title_source=builder.title_source,
        prompt=builder.prompt,
        catalog_source=builder.catalog_source,
        maps=[BuilderMapOptions(**item) for item in builder.maps],
        tag_names=builder.tag_names,
        target_count=builder.target_count,
        status=builder.status,
        succeeded_count=builder.succeeded_count,
        failed_count=builder.failed_count,
        pending_count=pending,
        created_by=builder.created_by,
        created_by_name=(names or {}).get(builder.created_by),
        created_at=builder.created_at,
        updated_at=builder.updated_at,
        finished_at=builder.finished_at,
    )


@router.post("", response_model=BuilderSessionSummary, status_code=status.HTTP_202_ACCEPTED)
async def create_builder_session(
    body: BuilderSessionCreate,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort = Depends(get_agent),
) -> BuilderSessionSummary:
    """Store the session and start generating in the background; poll GET /builder-sessions/{id} for progress."""
    builder = await create_session(session, actor, body)
    start_run(builder.id, actor, agent)
    return summary(builder, await creator_names(session, {builder.created_by}))


@router.get("", response_model=list[BuilderSessionSummary])
async def list_builder_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> list[BuilderSessionSummary]:
    rows = await session.scalars(
        select(BuilderSession)
        .where(BuilderSession.project_id == actor.project_id)
        .order_by(BuilderSession.created_at.desc(), BuilderSession.id.desc())
        .limit(limit)
    )
    items = rows.all()
    names = await creator_names(session, {item.created_by for item in items})
    pending = await pending_counts(session, actor.project_id, [item.id for item in items])
    return [summary(item, names, pending.get(item.id, 0)) for item in items]


@router.get("/{builder_id}", response_model=BuilderSessionDetail)
async def read_builder_session(
    builder_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> BuilderSessionDetail:
    builder = await get_session_row(session, actor.project_id, builder_id)
    cases = await session_cases(session, actor.project_id, builder_id)
    return BuilderSessionDetail(
        **summary(
            builder,
            await creator_names(session, {builder.created_by}),
            sum(1 for case in cases if case.status is TestCaseStatus.PENDING),
        ).model_dump(),
        errors=[BuilderError(**item) for item in builder.errors],
        test_cases=await case_responses(session, cases, actor),
    )
