from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.execution.schemas import RunJobResponse, RunResultListItem, RunResultPage, RunResultResponse, WorkerCompleteRequest, WorkerFailedRequest
from app.modules.identity.dependencies import Principal, require
from app.shared.config import get_settings
from app.shared.domain.errors import Conflict, Forbidden, NotFound
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import SessionFactory, get_session
from app.shared.infrastructure.models import RunJob, RunJobStatus, RunResult, RunVerdict, SuiteRunStatus, TestCase, TestSuiteRun

router = APIRouter(tags=["execution"])
# CARLA worker endpoints: authenticated by X-Worker-Token, outside any project URL.
worker_router = APIRouter(tags=["execution-worker"])


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


async def claim_next(worker_id: str) -> RunJob | None:
    async with SessionFactory() as session:
        # Jobs of Simulator Runner runs belong to their Bridge, not to this shared-token queue.
        bridge_runs = select(TestSuiteRun.id).where(TestSuiteRun.bridge_connection_id.is_not(None))
        job = await session.scalar(
            select(RunJob)
            .where(RunJob.status == RunJobStatus.QUEUED, or_(RunJob.suite_run_id.is_(None), RunJob.suite_run_id.not_in(bridge_runs)))
            .order_by(RunJob.queued_at, RunJob.id).with_for_update(skip_locked=True).limit(1)
        )
        if job is None: return None
        job.status, job.worker_id, job.claimed_at, job.attempt = RunJobStatus.CLAIMED, worker_id, datetime.now(UTC), job.attempt + 1
        await refresh_suite_status(session, job)
        await record_audit(session, project_id=job.project_id, actor_user_id=None, action="RUN_JOB_CLAIMED", entity_type="RUN_JOB", entity_id=job.id, after_data={"worker_id": worker_id})
        await session.commit()
        return job


def worker_token_valid(token: str | None) -> bool:
    return bool(token) and secrets.compare_digest(token.encode(), get_settings().worker_service_token.encode())


def verify_worker_token(token: str | None) -> None:
    if not worker_token_valid(token): raise Forbidden("Invalid worker service token")


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


@worker_router.post("/integration/worker/jobs/next")
async def next_job(x_worker_token: str | None = Header(default=None), x_worker_id: str = Header(default="worker")) -> dict:
    verify_worker_token(x_worker_token)
    job = await claim_next(x_worker_id)
    if job is None: return {"job": None}
    return {"job": {"id": job.id, "project_id": job.project_id, "test_case_id": job.test_case_id, "attempt": job.attempt}}


@worker_router.post("/integration/worker/jobs/{job_id}/started")
async def worker_started(job_id: int, x_worker_token: str | None = Header(default=None), session: AsyncSession = Depends(get_session)) -> dict:
    verify_worker_token(x_worker_token)
    job = await get_job(session, job_id, locked=True)
    if job.status is RunJobStatus.CLAIMED:
        job.status, job.started_at = RunJobStatus.RUNNING, datetime.now(UTC)
        await record_audit(session, project_id=job.project_id, actor_user_id=None, action="RUN_JOB_STARTED", entity_type="RUN_JOB", entity_id=job.id)
        await session.commit()
    return {"job_id": job.id, "status": job.status.value}


@worker_router.post("/integration/worker/jobs/{job_id}/completed", response_model=RunJobResponse)
async def worker_completed(job_id: int, body: WorkerCompleteRequest, x_worker_token: str | None = Header(default=None), session: AsyncSession = Depends(get_session)) -> RunJobResponse:
    verify_worker_token(x_worker_token)
    job = await get_job(session, job_id, locked=True)
    if job.result is not None: return serialize_job(job)  # idempotent worker retry
    if job.status not in {RunJobStatus.CLAIMED, RunJobStatus.RUNNING}: raise Conflict("Job cannot be completed from current state")
    job.status, job.finished_at = RunJobStatus.COMPLETED, datetime.now(UTC)
    case = await session.get(TestCase, job.test_case_id)
    # The case is locked since it entered the run, so its current configuration is what was executed.
    result = RunResult(project_id=job.project_id, run_job_id=job.id, test_case_id=job.test_case_id,
                       revision=case.revision if case else None, config_sha256=case.config_sha256 if case else None,
                       xosc_sha256=case.xosc_sha256 if case else None, verdict=body.verdict, metrics=body.metrics, scenario_runner_exit_code=body.scenario_runner_exit_code, duration_ms=body.duration_ms)
    session.add(result)
    await session.flush()
    await refresh_suite_status(session, job)
    await record_audit(session, project_id=job.project_id, actor_user_id=None, action="RUN_JOB_COMPLETED", entity_type="RUN_JOB", entity_id=job.id, after_data={"verdict": body.verdict.value})
    await session.commit()
    await session.refresh(job, attribute_names=["result"])
    return serialize_job(job)


@worker_router.post("/integration/worker/jobs/{job_id}/failed", response_model=RunJobResponse)
async def worker_failed(job_id: int, body: WorkerFailedRequest, x_worker_token: str | None = Header(default=None), session: AsyncSession = Depends(get_session)) -> RunJobResponse:
    verify_worker_token(x_worker_token)
    job = await get_job(session, job_id, locked=True)
    if job.status in {RunJobStatus.COMPLETED, RunJobStatus.CANCELLED, RunJobStatus.FAILED}: return serialize_job(job)  # idempotent worker retry
    if job.status not in {RunJobStatus.CLAIMED, RunJobStatus.RUNNING}: raise Conflict("Job cannot fail from current state")
    job.status, job.finished_at, job.error_code, job.error_message = RunJobStatus.FAILED, datetime.now(UTC), body.error_code, body.error_message
    await refresh_suite_status(session, job)
    await record_audit(session, project_id=job.project_id, actor_user_id=None, action="RUN_JOB_FAILED", entity_type="RUN_JOB", entity_id=job.id, after_data={"error_code": body.error_code})
    await session.commit()
    return serialize_job(job)


@worker_router.websocket("/integration/worker/ws")
async def worker_websocket(websocket: WebSocket) -> None:
    if not worker_token_valid(websocket.query_params.get("token")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    worker_id = "unknown"
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "worker.hello":
                worker_id = str(message.get("worker_id", "worker"))
                await websocket.send_json({"type": "worker.hello.ack"})
            elif message_type == "job.next":
                job = await claim_next(worker_id)
                await websocket.send_json({"type": "job.assigned", "job": None if job is None else {"id": job.id, "project_id": job.project_id, "test_case_id": job.test_case_id, "attempt": job.attempt}})
            elif message_type == "worker.heartbeat":
                await websocket.send_json({"type": "worker.heartbeat.ack"})
            elif message_type == "catalog.sync":
                # Seam for doc 18 / doc 19: a catalog belongs to the installation's project, which this
                # shared-token socket cannot identify. Once installations exist, validate the summary,
                # reply catalog.ack {known: hash already stored?}, and let the worker upload the full
                # catalog.v1 over HTTPS into app.modules.catalog.service.ingest_snapshot(source=WORKER).
                await websocket.send_json({"type": "error", "code": "INSTALLATION_REQUIRED", "ref_type": "catalog.sync"})
            else:
                await websocket.send_json({"type": "error", "code": "UNSUPPORTED_MESSAGE"})
    except WebSocketDisconnect:
        return
