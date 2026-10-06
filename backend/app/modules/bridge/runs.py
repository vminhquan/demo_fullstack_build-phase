"""Simulator Runner over the Bridge socket.

Send:    dispatch_local() builds run.assign (every test case of the run, XOSC inline) and sends it over the
         Bridge socket held by this process. The database is the source of truth: jobs stay QUEUED until the
         Bridge answers run.accepted, and unaccepted runs are re-sent (reconnect + maintenance loop).
Receive: handle_run_message() applies run.accepted / run.rejected / job.started / job.completed / job.failed /
         run.completed and returns the ack (or error) frame for the Bridge. Every message is idempotent.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bridge.hub import hub
from app.modules.bridge.schemas import (
    BridgeJobCompleted,
    BridgeJobFailed,
    BridgeJobStarted,
    BridgeRunMessage,
    BridgeRunRejected,
)
from app.modules.bridge.service import now
from app.modules.execution.router import refresh_suite_status
from app.shared.config import get_settings
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import SessionFactory
from app.shared.infrastructure.models import (
    Artifact,
    BridgeConnection,
    RunJob,
    RunJobStatus,
    RunResult,
    TestCase,
    TestSuiteRun,
)

logger = logging.getLogger(__name__)

ACTIVE = (RunJobStatus.QUEUED, RunJobStatus.CLAIMED, RunJobStatus.RUNNING)
TERMINAL = (RunJobStatus.COMPLETED, RunJobStatus.FAILED, RunJobStatus.CANCELLED)
RESEND_AFTER = timedelta(seconds=20)
MAINTENANCE_SECONDS = 15
RUN_MESSAGES = ("run.accepted", "run.rejected", "job.started", "job.completed", "job.failed", "run.completed")


# ---------------------------------------------------------------- send


async def build_assign(session: AsyncSession, run: TestSuiteRun) -> dict[str, Any] | None:
    """run.assign with every test case of the run that has no final result yet, grouped by map."""
    rows = (
        await session.execute(
            select(RunJob, TestCase, Artifact)
            .join(TestCase, TestCase.id == RunJob.test_case_id)
            .join(Artifact, Artifact.id == TestCase.xosc_artifact_id)
            .where(RunJob.suite_run_id == run.id, RunJob.status.in_(ACTIVE))
            .order_by(TestCase.map_code, RunJob.id)
        )
    ).all()
    if not rows:
        return None
    timeout = get_settings().bridge_job_timeout_seconds
    return {
        "type": "run.assign",
        "run_id": run.id,
        "project_id": run.project_id,
        "test_cases": [
            {
                "test_case_id": case.id,
                "case_key": case.case_key,
                "revision": case.revision,
                "map_name": case.map_code,
                "timeout_s": timeout,
                "xosc_sha256": artifact.sha256,
                "xosc": artifact.content.decode("utf-8"),
            }
            for _, case, artifact in rows
        ],
    }


async def dispatch_local(bridge_id: int, run_id: int) -> bool:
    """Sends run.assign over this process's socket for the Bridge. Registered as hub.on_dispatch."""
    async with SessionFactory() as session:
        run = await session.get(TestSuiteRun, run_id)
        connection = await session.get(BridgeConnection, run.bridge_connection_id) if run and run.bridge_connection_id else None
        if connection is None or connection.bridge_id != bridge_id or connection.revoked_at is not None:
            return False
        payload = await build_assign(session, run)
        if payload is None or not await hub._send_local(bridge_id, payload):
            return False
        run.dispatched_at = now()
        await session.commit()
    logger.info("run.assign %s sent to bridge %s (%d test cases)", run_id, bridge_id, len(payload["test_cases"]))
    return True


async def dispatch_pending(bridge_id: int) -> None:
    """On (re)connect: re-send every run of this Bridge that still has unfinished test cases.

    Accepted runs are included too; a Bridge that restarted without its state re-runs what is left,
    a Bridge that kept its state answers run.accepted again and goes on."""
    async with SessionFactory() as session:
        run_ids = (
            await session.scalars(
                select(TestSuiteRun.id)
                .join(BridgeConnection, BridgeConnection.id == TestSuiteRun.bridge_connection_id)
                .where(
                    BridgeConnection.bridge_id == bridge_id,
                    BridgeConnection.revoked_at.is_(None),
                    exists().where(RunJob.suite_run_id == TestSuiteRun.id, RunJob.status.in_(ACTIVE)),
                )
                .order_by(TestSuiteRun.id)
            )
        ).all()
    for run_id in run_ids:
        await dispatch_local(bridge_id, run_id)


async def resend_unaccepted() -> None:
    """run.assign that got no run.accepted (lost frame, Bridge busy reconnecting) is sent again."""
    for bridge_id in hub.local_bridge_ids():
        async with SessionFactory() as session:
            run_ids = (
                await session.scalars(
                    select(TestSuiteRun.id)
                    .join(BridgeConnection, BridgeConnection.id == TestSuiteRun.bridge_connection_id)
                    .where(
                        BridgeConnection.bridge_id == bridge_id,
                        BridgeConnection.revoked_at.is_(None),
                        TestSuiteRun.accepted_at.is_(None),
                        or_(TestSuiteRun.dispatched_at.is_(None), TestSuiteRun.dispatched_at < now() - RESEND_AFTER),
                        exists().where(RunJob.suite_run_id == TestSuiteRun.id, RunJob.status.in_(ACTIVE)),
                    )
                )
            ).all()
        for run_id in run_ids:
            await dispatch_local(bridge_id, run_id)


async def fail_timed_out() -> None:
    """A test case RUNNING well past its timeout never reported back: fail it so the run can finish."""
    limit = now() - timedelta(seconds=get_settings().bridge_job_timeout_seconds + 60)
    async with SessionFactory() as session:
        jobs = (
            await session.scalars(
                select(RunJob)
                .join(TestSuiteRun, TestSuiteRun.id == RunJob.suite_run_id)
                .where(TestSuiteRun.bridge_connection_id.is_not(None), RunJob.status == RunJobStatus.RUNNING, RunJob.started_at < limit)
                .with_for_update(of=RunJob, skip_locked=True)
            )
        ).all()
        for job in jobs:
            job.status, job.finished_at, job.error_code = RunJobStatus.FAILED, now(), "TIMEOUT"
            job.error_message = "Bridge không gửi kết quả trong thời gian cho phép"
            await refresh_suite_status(session, job)
            await record_audit(session, project_id=job.project_id, actor_user_id=None, action="RUN_JOB_FAILED",
                               entity_type="RUN_JOB", entity_id=job.id, after_data={"error_code": "TIMEOUT"})
        await session.commit()
    for job in jobs:
        await hub.publish_project(job.project_id, {"type": "run.updated", "run_id": job.suite_run_id, "test_case_id": job.test_case_id})


async def maintenance_loop() -> None:
    while True:
        await asyncio.sleep(MAINTENANCE_SECONDS)
        try:
            await resend_unaccepted()
            await fail_timed_out()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - e.g. database restarting; try again next tick
            logger.exception("Simulator Runner maintenance failed")


hub.on_dispatch = dispatch_local


# ---------------------------------------------------------------- receive


def _ack(kind: str, run_id: int, test_case_id: int | None = None, *, duplicate: bool = False) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "ack", "ref": kind, "run_id": run_id}
    if test_case_id is not None:
        frame["test_case_id"] = test_case_id
    if duplicate:
        frame["duplicate"] = True
    return frame


def _error(kind: str, code: str, message: str, raw: dict[str, Any]) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "error", "ref": kind, "code": code, "message": message}
    for key in ("run_id", "test_case_id"):
        if isinstance(raw.get(key), int):
            frame[key] = raw[key]
    return frame


async def _owned_run(session: AsyncSession, bridge_id: int, run_id: int) -> TestSuiteRun | None:
    """The run, locked, only if it was sent to a live connection of this Bridge."""
    run = await session.get(TestSuiteRun, run_id, with_for_update=True)
    if run is None or run.bridge_connection_id is None:
        return None
    connection = await session.get(BridgeConnection, run.bridge_connection_id)
    if connection is None or connection.bridge_id != bridge_id or connection.revoked_at is not None:
        return None
    return run


async def _finish_job(session: AsyncSession, job: RunJob, status: RunJobStatus, code: str | None = None, message: str | None = None) -> None:
    job.status, job.finished_at = status, now()
    job.error_code, job.error_message = code, message
    await refresh_suite_status(session, job)
    action = "RUN_JOB_COMPLETED" if status is RunJobStatus.COMPLETED else "RUN_JOB_FAILED"
    await record_audit(session, project_id=job.project_id, actor_user_id=None, action=action, entity_type="RUN_JOB",
                       entity_id=job.id, after_data={"error_code": code} if code else None)


async def handle_run_message(bridge_id: int, message: dict[str, Any]) -> dict[str, Any]:
    kind = str(message.get("type"))
    try:
        async with SessionFactory() as session:
            reply, project_id, run_id = await _apply(session, bridge_id, kind, message)
            await session.commit()
    except ValidationError as exc:
        return _error(kind, "INVALID_MESSAGE", str(exc)[:1000], message)
    if project_id is not None and not reply.get("duplicate") and reply["type"] == "ack":
        await hub.publish_project(project_id, {"type": "run.updated", "run_id": run_id, "test_case_id": message.get("test_case_id")})
    return reply


async def _apply(session: AsyncSession, bridge_id: int, kind: str, message: dict[str, Any]) -> tuple[dict[str, Any], int | None, int | None]:
    if kind in ("run.accepted", "run.completed"):
        body = BridgeRunMessage.model_validate(message)
    elif kind == "run.rejected":
        body = BridgeRunRejected.model_validate(message)
    elif kind == "job.started":
        body = BridgeJobStarted.model_validate(message)
    elif kind == "job.completed":
        body = BridgeJobCompleted.model_validate(message)
    else:
        body = BridgeJobFailed.model_validate(message)

    run = await _owned_run(session, bridge_id, body.run_id)
    if run is None:
        return _error(kind, "RUN_NOT_FOUND", "Run does not exist or is not assigned to this Bridge", message), None, None

    if kind in ("run.accepted", "run.rejected", "run.completed"):
        jobs = (await session.scalars(select(RunJob).where(RunJob.suite_run_id == run.id).with_for_update())).all()
        active = [job for job in jobs if job.status in ACTIVE]
        if kind == "run.accepted":
            connection = await session.get(BridgeConnection, run.bridge_connection_id)
            first = run.accepted_at is None
            run.accepted_at = now()
            for job in active:
                if job.status is RunJobStatus.QUEUED:
                    job.status, job.claimed_at, job.attempt = RunJobStatus.CLAIMED, now(), job.attempt + 1
                    job.worker_id = f"bridge:{connection.uid}"
            if active:
                await refresh_suite_status(session, active[0])
            if first:
                await record_audit(session, project_id=run.project_id, actor_user_id=None, action="SIMULATOR_RUN_ACCEPTED",
                                   entity_type="TEST_SUITE_RUN", entity_id=run.id)
            return _ack(kind, run.id, duplicate=not first), run.project_id, run.id
        if kind == "run.rejected":
            for job in active:
                await _finish_job(session, job, RunJobStatus.FAILED, body.reason, body.message)
            return _ack(kind, run.id, duplicate=not active), run.project_id, run.id
        # run.completed: anything the Bridge did not report is failed so the run can close.
        for job in active:
            await _finish_job(session, job, RunJobStatus.FAILED, "MISSING_RESULT", "Bridge kết thúc phiên nhưng không gửi kết quả test case này")
        return _ack(kind, run.id), run.project_id, run.id

    job = await session.scalar(
        select(RunJob).where(RunJob.suite_run_id == run.id, RunJob.test_case_id == body.test_case_id).with_for_update()
    )
    if job is None:
        return _error(kind, "TEST_CASE_NOT_IN_RUN", "Test case is not part of this run", message), None, None
    if job.status in TERMINAL:
        # A re-sent result (lost ack): already settled.
        return _ack(kind, run.id, job.test_case_id, duplicate=True), run.project_id, run.id

    if kind == "job.started":
        if job.status is RunJobStatus.RUNNING:
            return _ack(kind, run.id, job.test_case_id, duplicate=True), run.project_id, run.id
        if job.status is RunJobStatus.QUEUED:  # run.accepted was lost
            job.attempt += 1
        job.status, job.started_at = RunJobStatus.RUNNING, now()
        await refresh_suite_status(session, job)
        return _ack(kind, run.id, job.test_case_id), run.project_id, run.id

    if kind == "job.failed":
        await _finish_job(session, job, RunJobStatus.FAILED, body.error_code, body.error_message)
        return _ack(kind, run.id, job.test_case_id), run.project_id, run.id

    # job.completed
    case = await session.get(TestCase, job.test_case_id)
    artifact = await session.get(Artifact, case.xosc_artifact_id) if case.xosc_artifact_id else None
    expected_sha = artifact.sha256 if artifact else case.xosc_sha256
    if body.revision != case.revision or body.xosc_sha256 != expected_sha:
        detail = f"Bridge chạy revision {body.revision} / xosc {body.xosc_sha256[:12]}, test case là revision {case.revision} / xosc {(expected_sha or '')[:12]}"
        await _finish_job(session, job, RunJobStatus.FAILED, "RESULT_MISMATCH", detail)
        return _ack(kind, run.id, job.test_case_id), run.project_id, run.id
    if job.started_at is None:
        job.started_at = now()
    session.add(
        RunResult(
            project_id=job.project_id, run_job_id=job.id, test_case_id=case.id,
            revision=case.revision, config_sha256=case.config_sha256, xosc_sha256=expected_sha,
            verdict=body.verdict, metrics={**body.metrics, **({"map_name": body.map_name} if body.map_name else {})},
            scenario_runner_exit_code=body.exit_code, duration_ms=body.duration_ms,
        )
    )
    await _finish_job(session, job, RunJobStatus.COMPLETED)
    return _ack(kind, run.id, job.test_case_id), run.project_id, run.id

