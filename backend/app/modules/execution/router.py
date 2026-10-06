from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.execution.schemas import RunJobResponse, RunResultListItem, RunResultPage, RunResultResponse
from app.modules.identity.dependencies import Principal, require
from app.shared.domain.errors import Conflict, NotFound
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import RunJob, RunJobStatus, RunResult, RunVerdict, SuiteRunStatus, TestCase, TestSuiteRun

router = APIRouter(tags=["execution"])


def serialize_result(result: RunResult | None) -> RunResultResponse | None:
    if result is None: return None
    return RunResultResponse(id=result.id, run_job_id=result.run_job_id, test_case_id=result.test_case_id, revision=result.revision, verdict=result.verdict, metrics=result.metrics, scenario_runner_exit_code=result.scenario_runner_exit_code, duration_ms=result.duration_ms)


def serialize_job(job: RunJob) -> RunJobResponse:
    return RunJobResponse(id=job.id, suite_run_id=job.suite_run_id, test_case_id=job.test_case_id, status=job.status.value, worker_id=job.worker_id, attempt=job.attempt, error_code=job.error_code, error_message=job.error_message, result=serialize_result(job.result))


def result_list_item(result: RunResult, case: TestCase) -> RunResultListItem:
    metrics = result.metrics or {}
    raw_ttc = metrics.get("min_ttc_seconds")
    return RunResultListItem(
        id=result.id,
        run_job_id=result.run_job_id,
        revision=result.revision,
        test_case_id=case.id,
        case_key=case.case_key,
        title=case.title,
        map_code=case.map_code,
        adversary_type=case.adversary_type,
        environment_code=case.environment_code,
        verdict=result.verdict,
        metrics=metrics,
        scenario_runner_exit_code=result.scenario_runner_exit_code,
        duration_ms=result.duration_ms,
        collision=bool(metrics.get("collision", False)) or int(metrics.get("collision_count", 0)) > 0,
        min_ttc_seconds=float(raw_ttc) if isinstance(raw_ttc, int | float) else None,
        created_at=result.created_at,
    )


async def get_job(session: AsyncSession, job_id: int, *, project_id: int | None = None, locked: bool = False) -> RunJob:
    statement = select(RunJob).where(RunJob.id == job_id).options(selectinload(RunJob.result))
    if project_id is not None:
        statement = statement.where(RunJob.project_id == project_id)
    if locked: statement = statement.with_for_update()
    job = await session.scalar(statement)
    if job is None: raise NotFound("Run job not found")
    return job


async def refresh_suite_status(session: AsyncSession, job: RunJob) -> None:
    if job.suite_run_id is None: return
    suite_run = await session.get(TestSuiteRun, job.suite_run_id, with_for_update=True)
    if suite_run is None: return
    terminal = (await session.scalars(select(RunJob.status).where(RunJob.suite_run_id == suite_run.id))).all()
    suite_run.completed_jobs = sum(value in {RunJobStatus.COMPLETED, RunJobStatus.FAILED, RunJobStatus.CANCELLED} for value in terminal)
    if suite_run.completed_jobs == suite_run.total_jobs:
        suite_run.finished_at = datetime.now(UTC)
        if any(value is RunJobStatus.FAILED for value in terminal):
            suite_run.status = SuiteRunStatus.FAILED
        elif all(value is RunJobStatus.CANCELLED for value in terminal):
            suite_run.status = SuiteRunStatus.CANCELLED
        else:
            suite_run.status = SuiteRunStatus.COMPLETED
    elif suite_run.status is SuiteRunStatus.QUEUED:
        suite_run.status, suite_run.started_at = SuiteRunStatus.RUNNING, datetime.now(UTC)


@router.get("/run-jobs/{job_id}", response_model=RunJobResponse)
async def read_job(job_id: int, actor: Principal = Depends(require("suite:read")), session: AsyncSession = Depends(get_session)) -> RunJobResponse:
    return serialize_job(await get_job(session, job_id, project_id=actor.project_id))


@router.get("/run-results/{result_id}", response_model=RunResultResponse)
async def read_result(result_id: int, actor: Principal = Depends(require("suite:read")), session: AsyncSession = Depends(get_session)) -> RunResultResponse:
    result = await session.scalar(select(RunResult).where(RunResult.id == result_id, RunResult.project_id == actor.project_id))
    if result is None: raise NotFound("Run result not found")
    return serialize_result(result)


@router.get("/run-results", response_model=RunResultPage)
async def list_results(
    verdict: RunVerdict | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: Principal = Depends(require("suite:read")),
    session: AsyncSession = Depends(get_session),
) -> RunResultPage:
    statement = (
        select(RunResult, TestCase)
        .join(TestCase, RunResult.test_case_id == TestCase.id)
        .where(RunResult.project_id == actor.project_id, TestCase.archived_at.is_(None))
        .order_by(RunResult.created_at.desc())
    )
    if verdict:
        statement = statement.where(RunResult.verdict == verdict)
    rows = (await session.execute(statement)).tuples().all()
    start = (page - 1) * page_size
    return RunResultPage(
        items=[result_list_item(result, case) for result, case in rows[start:start + page_size]],
        page=page,
        page_size=page_size,
        total=len(rows),
    )


@router.post("/run-jobs/{job_id}/cancel", response_model=RunJobResponse)
async def cancel_job(job_id: int, actor: Principal = Depends(require("suite:run")), session: AsyncSession = Depends(get_session)) -> RunJobResponse:
    job = await get_job(session, job_id, project_id=actor.project_id, locked=True)
    if job.status not in {RunJobStatus.QUEUED, RunJobStatus.CLAIMED}: raise Conflict("Only queued or claimed jobs can be cancelled")
    job.status, job.finished_at = RunJobStatus.CANCELLED, datetime.now(UTC)
    await refresh_suite_status(session, job)
    await record_audit(session, project_id=actor.project_id, actor_user_id=actor.id, action="RUN_JOB_CANCELLED", entity_type="RUN_JOB", entity_id=job.id)
    await session.commit()
    return serialize_job(job)
