from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import Principal, require
from app.modules.testsuite.schemas import (
    AddSuiteItem,
    ReorderItems,
    RunJobSummary,
    SuiteCreate,
    SuiteItemResponse,
    SuiteResponse,
    SuiteRunResponse,
    SuiteUpdate,
)
from app.shared.domain.errors import Conflict, NotFound
from app.modules.testcase.service import lock_for_run
from app.shared.domain.policies import ensure_suite_eligible
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    RunJob,
    SuiteRunStatus,
    TestCase,
    TestSuite,
    TestSuiteItem,
    TestSuiteRun,
)

router = APIRouter(prefix="/test-suites", tags=["test-suites"])
suite_runs_router = APIRouter(prefix="/suite-runs", tags=["suite-runs"])


def serialize_item(item: TestSuiteItem) -> SuiteItemResponse:
    return SuiteItemResponse(
        test_case_id=item.test_case_id,
        position=item.position,
        added_by=item.added_by,
        added_at=item.added_at,
    )


def serialize_suite(suite: TestSuite) -> SuiteResponse:
    return SuiteResponse(
        id=suite.id,
        name=suite.name,
        description=suite.description,
        created_by=suite.created_by,
        created_at=suite.created_at,
        updated_at=suite.updated_at,
        items=[
            serialize_item(item)
            for item in sorted(suite.items, key=lambda value: value.position)
        ],
    )


def serialize_run(run: TestSuiteRun) -> SuiteRunResponse:
    return SuiteRunResponse(
        id=run.id,
        suite_id=run.suite_id,
        status=run.status,
        total_jobs=run.total_jobs,
        completed_jobs=run.completed_jobs,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def get_suite(
    session: AsyncSession,
    project_id: int,
    suite_id: int,
    *,
    locked: bool = False,
) -> TestSuite:
    statement = (
        select(TestSuite)
        .where(TestSuite.id == suite_id, TestSuite.project_id == project_id)
        .options(selectinload(TestSuite.items))
    )
    if locked:
        statement = statement.with_for_update()
    suite = await session.scalar(statement)
    if suite is None:
        raise NotFound("Test suite not found")
    return suite


@router.post("", response_model=SuiteResponse, status_code=status.HTTP_201_CREATED)
async def create_suite(
    body: SuiteCreate,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    suite = TestSuite(
        project_id=actor.project_id,
        name=body.name,
        description=body.description,
        created_by=actor.id,
        items=[],
    )
    session.add(suite)
    await session.flush()
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_CREATED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
    )
    await session.commit()
    return serialize_suite(suite)


@router.get("", response_model=list[SuiteResponse])
async def list_suites(
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> list[SuiteResponse]:
    suites = (
        await session.scalars(
            select(TestSuite)
            .where(TestSuite.project_id == actor.project_id)
            .options(selectinload(TestSuite.items))
            .order_by(TestSuite.updated_at.desc())
        )
    ).all()
    return [serialize_suite(item) for item in suites]


@router.get("/{suite_id}", response_model=SuiteResponse)
async def read_suite(
    suite_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    return serialize_suite(await get_suite(session, actor.project_id, suite_id))


@router.patch("/{suite_id}", response_model=SuiteResponse)
async def patch_suite(
    suite_id: int,
    body: SuiteUpdate,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    if body.name is not None:
        suite.name = body.name
    if body.description is not None:
        suite.description = body.description
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_UPDATED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
    )
    await session.commit()
    return serialize_suite(suite)


@router.delete("/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suite(
    suite_id: int,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    has_runs = await session.scalar(
        select(TestSuiteRun.id).where(TestSuiteRun.suite_id == suite.id).limit(1)
    )
    if has_runs is not None:
        raise Conflict("Suite has run history and cannot be deleted")
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_DELETED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
    )
    await session.delete(suite)
    await session.commit()


@router.post("/{suite_id}/items", response_model=SuiteResponse)
async def add_item(
    suite_id: int,
    body: AddSuiteItem,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    case = await session.scalar(
        select(TestCase)
        .where(TestCase.id == body.test_case_id, TestCase.project_id == actor.project_id)
        .with_for_update()
    )
    if case is None:
        raise NotFound("Test case not found")
    ensure_suite_eligible(case.status)
    if any(item.test_case_id == case.id for item in suite.items):
        raise Conflict("Test case is already in this suite")
    suite.items.append(
        TestSuiteItem(
            project_id=actor.project_id,
            test_case_id=case.id,
            position=max((item.position for item in suite.items), default=-1) + 1,
            added_by=actor.id,
        )
    )
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_ITEM_ADDED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
        after_data={"test_case_id": case.id},
    )
    await session.commit()
    return serialize_suite(suite)


@router.delete("/{suite_id}/items/{test_case_id}", response_model=SuiteResponse)
async def remove_item(
    suite_id: int,
    test_case_id: int,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    item = next(
        (value for value in suite.items if value.test_case_id == test_case_id), None
    )
    if item is None:
        raise NotFound("Test suite item not found")
    suite.items.remove(item)
    for position, value in enumerate(
        sorted(suite.items, key=lambda candidate: candidate.position)
    ):
        value.position = position
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_ITEM_REMOVED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
        before_data={"test_case_id": test_case_id},
    )
    await session.commit()
    return serialize_suite(suite)


@router.patch("/{suite_id}/items/reorder", response_model=SuiteResponse)
async def reorder_items(
    suite_id: int,
    body: ReorderItems,
    actor: Principal = Depends(require("suite:manage")),
    session: AsyncSession = Depends(get_session),
) -> SuiteResponse:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    existing = {item.test_case_id: item for item in suite.items}
    if len(body.test_case_ids) != len(existing) or set(body.test_case_ids) != set(existing):
        raise Conflict("Reorder request must include every current suite item exactly once")
    for position, case_id in enumerate(body.test_case_ids):
        existing[case_id].position = position
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_ITEMS_REORDERED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
    )
    await session.commit()
    return serialize_suite(suite)


@router.post("/{suite_id}/runs", response_model=SuiteRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_suite(
    suite_id: int,
    actor: Principal = Depends(require("suite:run")),
    session: AsyncSession = Depends(get_session),
) -> SuiteRunResponse:
    suite = await get_suite(session, actor.project_id, suite_id, locked=True)
    ordered = [item.test_case_id for item in sorted(suite.items, key=lambda value: value.position)]
    # Putting cases into a run locks them for good; cases edited back to PENDING are skipped.
    runnable, skipped = await lock_for_run(session, actor.project_id, ordered, actor.id)
    if not runnable:
        raise Conflict("A suite run needs at least one APPROVED test case", {"skipped": [case_id for case_id, _ in skipped]})
    run = TestSuiteRun(
        project_id=actor.project_id,
        suite_id=suite.id,
        requested_by=actor.id,
        status=SuiteRunStatus.QUEUED,
        total_jobs=len(runnable),
    )
    session.add(run)
    await session.flush()
    for case in runnable:
        session.add(
            RunJob(
                project_id=actor.project_id,
                suite_run_id=run.id,
                test_case_id=case.id,
                idempotency_key=f"suite:{run.id}:case:{case.id}",
            )
        )
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_RUN_REQUESTED",
        entity_type="TEST_SUITE_RUN",
        entity_id=run.id,
        after_data={"total_jobs": run.total_jobs, "skipped": [case_id for case_id, _ in skipped]},
    )
    await session.commit()
    return serialize_run(run)


@router.get("/{suite_id}/runs", response_model=list[SuiteRunResponse])
async def list_suite_runs(
    suite_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> list[SuiteRunResponse]:
    await get_suite(session, actor.project_id, suite_id)
    runs = (
        await session.scalars(
            select(TestSuiteRun)
            .where(
                TestSuiteRun.project_id == actor.project_id,
                TestSuiteRun.suite_id == suite_id,
            )
            .order_by(TestSuiteRun.created_at.desc())
        )
    ).all()
    return [serialize_run(run) for run in runs]


@router.post("/{suite_id}/export")
async def export_suite(
    suite_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    suite = await get_suite(session, actor.project_id, suite_id)
    case_ids = [item.test_case_id for item in sorted(suite.items, key=lambda value: value.position)]
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SUITE_EXPORT_REQUESTED",
        entity_type="TEST_SUITE",
        entity_id=suite.id,
        after_data={"test_case_ids": case_ids},
    )
    await session.commit()
    return {
        "suite_id": suite.id,
        "suite_name": suite.name,
        "test_case_ids": case_ids,
        "state": "EXPORT_QUEUED",
    }


@suite_runs_router.get("/{suite_run_id}", response_model=SuiteRunResponse)
async def read_suite_run(
    suite_run_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> SuiteRunResponse:
    run = await session.scalar(
        select(TestSuiteRun).where(
            TestSuiteRun.id == suite_run_id,
            TestSuiteRun.project_id == actor.project_id,
        )
    )
    if run is None:
        raise NotFound("Suite run not found")
    return serialize_run(run)


@suite_runs_router.get("/{suite_run_id}/jobs", response_model=list[RunJobSummary])
async def list_run_jobs(
    suite_run_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> list[RunJobSummary]:
    jobs = (
        await session.scalars(
            select(RunJob)
            .where(
                RunJob.project_id == actor.project_id,
                RunJob.suite_run_id == suite_run_id,
            )
            .order_by(RunJob.queued_at, RunJob.id)
        )
    ).all()
    return [
        RunJobSummary(
            id=job.id,
            test_case_id=job.test_case_id,
            status=job.status,
            attempt=job.attempt,
            error_code=job.error_code,
            error_message=job.error_message,
        )
        for job in jobs
    ]
