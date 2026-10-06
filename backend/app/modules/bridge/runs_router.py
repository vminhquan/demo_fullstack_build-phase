"""Simulator Runner: run approved test cases on the project's Bridge (CARLA on the user's machine)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bridge.hub import hub
from app.modules.bridge.schemas import (
    CaseRunEntry,
    SimulatorRunBridge,
    SimulatorRunCreate,
    SimulatorRunDetail,
    SimulatorRunJob,
    SimulatorRunSummary,
)
from app.modules.bridge.service import bridge_online
from app.modules.identity.dependencies import Principal, require
from app.modules.testcase.service import lock_for_run, user_names
from app.shared.domain.errors import Conflict, NotFound
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    BridgeConnection,
    RunJob,
    RunJobStatus,
    RunResult,
    RunVerdict,
    SuiteRunStatus,
    TestCase,
    TestSuiteRun,
)

router = APIRouter(prefix="/simulator-runs", tags=["simulator-runs"])

ACTIVE = (RunJobStatus.QUEUED, RunJobStatus.CLAIMED, RunJobStatus.RUNNING)


def job_view(job: RunJob, case: TestCase, result: RunResult | None) -> SimulatorRunJob:
    metrics = (result.metrics if result else None) or {}
    ttc = metrics.get("min_ttc_seconds")
    return SimulatorRunJob(
        test_case_id=case.id, case_key=case.case_key, title=case.title, map_code=case.map_code,
        adversary_type=case.adversary_type, environment_code=case.environment_code, danger_level=case.danger_level,
        revision=result.revision if result else case.revision, status=job.status,
        verdict=result.verdict if result else None,
        collision=(bool(metrics.get("collision")) or int(metrics.get("collision_count") or 0) > 0) if result else None,
        min_ttc_seconds=float(ttc) if isinstance(ttc, int | float) else None,
        duration_ms=result.duration_ms if result else None, exit_code=result.scenario_runner_exit_code if result else None,
        metrics=metrics, error_code=job.error_code, error_message=job.error_message,
        started_at=job.started_at, finished_at=job.finished_at,
    )


async def run_views(session: AsyncSession, runs: list[TestSuiteRun], *, with_jobs: bool) -> list[SimulatorRunDetail]:
    if not runs:
        return []
    run_ids = [run.id for run in runs]
    rows = (
        await session.execute(
            select(RunJob, TestCase, RunResult)
            .join(TestCase, TestCase.id == RunJob.test_case_id)
            .outerjoin(RunResult, RunResult.run_job_id == RunJob.id)
            .where(RunJob.suite_run_id.in_(run_ids))
            .order_by(TestCase.map_code, RunJob.id)
        )
    ).all()
    connection_ids = {run.bridge_connection_id for run in runs if run.bridge_connection_id}
    connections = {
        item.id: item
        for item in (await session.scalars(select(BridgeConnection).where(BridgeConnection.id.in_(connection_ids)))).all()
    } if connection_ids else {}
    names = await user_names(session, [run.requested_by for run in runs])
    views = []
    for run in runs:
        jobs = [(job, case, result) for job, case, result in rows if job.suite_run_id == run.id]
        connection = connections.get(run.bridge_connection_id or 0)
        verdicts = [result.verdict for _, _, result in jobs if result is not None]
        views.append(
            SimulatorRunDetail(
                id=run.id, status=run.status, created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
                dispatched_at=run.dispatched_at, accepted_at=run.accepted_at,
                requested_by=run.requested_by, requested_by_name=names.get(run.requested_by),
                bridge=SimulatorRunBridge(connection_uid=connection.uid, name=connection.bridge.name, online=bridge_online(connection.bridge))
                if connection else None,
                total=len(jobs),
                done=sum(job.status not in ACTIVE for job, _, _ in jobs),
                passed=verdicts.count(RunVerdict.PASS),
                failed=verdicts.count(RunVerdict.FAIL),
                errors=verdicts.count(RunVerdict.ERROR) + sum(job.status is RunJobStatus.FAILED for job, _, _ in jobs),
                test_case_ids=[case.id for _, case, _ in jobs],
                jobs=[job_view(job, case, result) for job, case, result in jobs] if with_jobs else [],
            )
        )
    return views


async def get_run(session: AsyncSession, project_id: int, run_id: int, *, locked: bool = False) -> TestSuiteRun:
    run = await session.get(TestSuiteRun, run_id, with_for_update=locked)
    if run is None or run.project_id != project_id or run.bridge_connection_id is None:
        raise NotFound("Simulator run not found")
    return run


@router.post("", response_model=SimulatorRunDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_simulator_run(
    body: SimulatorRunCreate,
    actor: Principal = Depends(require("suite:run")),
    session: AsyncSession = Depends(get_session),
) -> SimulatorRunDetail:
    """Locks the approved test cases, queues one job each and sends the whole run to the Bridge (run.assign)."""
    connection = await session.scalar(
        select(BridgeConnection).where(
            BridgeConnection.uid == body.connection_uid, BridgeConnection.project_id == actor.project_id, BridgeConnection.revoked_at.is_(None)
        )
    )
    if connection is None:
        raise NotFound("Bridge connection not found")
    wanted = list(dict.fromkeys(body.test_case_ids))
    # A case without a .xosc file cannot run; leave it unlocked.
    no_xosc = set(
        (await session.scalars(
            select(TestCase.id).where(TestCase.project_id == actor.project_id, TestCase.id.in_(wanted), TestCase.xosc_artifact_id.is_(None))
        )).all()
    )
    runnable, skipped = await lock_for_run(session, actor.project_id, [case_id for case_id in wanted if case_id not in no_xosc], actor.id)
    skipped = [*skipped, *((case_id, "NO_XOSC") for case_id in wanted if case_id in no_xosc)]
    if not runnable:
        raise Conflict("Không có test case nào chạy được (cần Đã phê duyệt và có tệp .xosc)", {"skipped": [{"id": i, "reason": r} for i, r in skipped]})
    run = TestSuiteRun(
        project_id=actor.project_id, suite_id=None, requested_by=actor.id, status=SuiteRunStatus.QUEUED,
        total_jobs=len(runnable), bridge_connection_id=connection.id,
    )
    session.add(run)
    await session.flush()
    for case in sorted(runnable, key=lambda item: item.map_code):  # job order = run order: grouped by map
        session.add(RunJob(project_id=actor.project_id, suite_run_id=run.id, test_case_id=case.id, idempotency_key=f"sim:{run.id}:case:{case.id}"))
    await record_audit(
        session, project_id=actor.project_id, actor_user_id=actor.id, action="SIMULATOR_RUN_REQUESTED", entity_type="TEST_SUITE_RUN",
        entity_id=run.id, after_data={"connection_uid": connection.uid, "total": len(runnable), "skipped": [i for i, _ in skipped]},
    )
    await session.commit()
    await hub.request_dispatch(connection.bridge_id, run.id)
    await session.refresh(run)
    view = (await run_views(session, [run], with_jobs=True))[0]
    view.skipped = [{"id": case_id, "reason": reason} for case_id, reason in skipped]
    return view


@router.get("", response_model=list[SimulatorRunSummary])
async def list_simulator_runs(
    limit: int = Query(default=100, ge=1, le=500),
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> list[SimulatorRunSummary]:
    runs = (
        await session.scalars(
            select(TestSuiteRun)
            .where(TestSuiteRun.project_id == actor.project_id, TestSuiteRun.bridge_connection_id.is_not(None))
            .order_by(TestSuiteRun.id.desc())
            .limit(limit)
        )
    ).all()
    return [SimulatorRunSummary(**view.model_dump(exclude={"jobs", "skipped"})) for view in await run_views(session, list(runs), with_jobs=False)]


@router.get("/by-case/{case_id}", response_model=list[CaseRunEntry])
async def case_runs(
    case_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> list[CaseRunEntry]:
    rows = (
        await session.execute(
            select(TestSuiteRun, RunJob, TestCase, RunResult)
            .join(RunJob, RunJob.suite_run_id == TestSuiteRun.id)
            .join(TestCase, TestCase.id == RunJob.test_case_id)
            .outerjoin(RunResult, RunResult.run_job_id == RunJob.id)
            .where(TestSuiteRun.project_id == actor.project_id, TestSuiteRun.bridge_connection_id.is_not(None), RunJob.test_case_id == case_id)
            .order_by(TestSuiteRun.id.desc())
        )
    ).all()
    names = await user_names(session, [run.requested_by for run, *_ in rows])
    return [
        CaseRunEntry(run_id=run.id, created_at=run.created_at, requested_by_name=names.get(run.requested_by), job=job_view(job, case, result))
        for run, job, case, result in rows
    ]


@router.get("/{run_id}", response_model=SimulatorRunDetail)
async def read_simulator_run(
    run_id: int,
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> SimulatorRunDetail:
    run = await get_run(session, actor.project_id, run_id)
    return (await run_views(session, [run], with_jobs=True))[0]

