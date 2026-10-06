from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.shared.infrastructure.models import DangerLevel, RunJobStatus, RunVerdict, SuiteRunStatus


class PairCodeResponse(BaseModel):
    id: int
    code: str
    expires_at: datetime
    ttl_seconds: int


class CarlaProbe(BaseModel):
    host: str = Field(default="127.0.0.1", max_length=255)
    port: int = Field(default=2000, ge=1, le=65535)
    reachable: bool


class BridgePairRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(default="", max_length=120)
    hostname: str = Field(default="", max_length=255)
    os: str = Field(default="", max_length=120)
    bridge_version: str = Field(default="", max_length=32)
    carla: CarlaProbe | None = None


class ConnectionInfo(BaseModel):
    connection_uid: str
    project_id: int
    project_name: str
    paired_at: datetime


class BridgePairResponse(BaseModel):
    bridge_uid: str
    # Only returned when this pairing registered the machine; later pairings reuse the stored token.
    device_token: str | None
    connection: ConnectionInfo


class BridgeConnectionResponse(BaseModel):
    connection_uid: str
    bridge_uid: str
    name: str
    hostname: str
    os: str
    bridge_version: str
    online: bool
    carla_host: str | None
    carla_port: int | None
    carla_reachable: bool | None
    last_seen_at: datetime | None
    paired_at: datetime
    paired_by: int
    paired_by_name: str | None
    # CARLA data this Bridge synced into the project (catalog snapshots with source WORKER).
    synced_maps: list[str] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    # Live progress of a sync in flight (or the last one finished since the backend started), else None.
    sync: dict | None = None


class CatalogSyncRequest(BaseModel):
    # Map names to read; None = every map the user's CARLA has.
    maps: list[str] | None = Field(default=None, max_length=100)


class CatalogSyncStarted(BaseModel):
    request_id: str


class BridgeCatalogUpload(BaseModel):
    connection_uid: str = Field(max_length=32)
    request_id: str | None = Field(default=None, max_length=64)
    catalog: dict


class BridgeCatalogStored(BaseModel):
    snapshot_id: int
    map_name: str
    created: bool


# ---- Simulator Runner over the Bridge socket (Bridge -> backend) ----


class BridgeRunMessage(BaseModel):
    """run.accepted / run.completed."""

    run_id: int


class BridgeRunRejected(BaseModel):
    run_id: int
    reason: str = Field(min_length=1, max_length=120)
    message: str | None = Field(default=None, max_length=4000)


class BridgeJobStarted(BaseModel):
    run_id: int
    test_case_id: int


class BridgeJobCompleted(BaseModel):
    run_id: int
    test_case_id: int
    # Echo of what was run, checked against the locked test case.
    revision: int
    xosc_sha256: str = Field(min_length=64, max_length=64)
    map_name: str | None = Field(default=None, max_length=120)
    verdict: RunVerdict
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class BridgeJobFailed(BaseModel):
    run_id: int
    test_case_id: int
    error_code: str = Field(min_length=1, max_length=120)
    error_message: str | None = Field(default=None, max_length=4000)


# ---- Simulator Runner API (frontend) ----


class SimulatorRunCreate(BaseModel):
    connection_uid: str = Field(min_length=1, max_length=32)
    test_case_ids: list[int] = Field(min_length=1, max_length=500)


class SimulatorRunBridge(BaseModel):
    connection_uid: str
    name: str
    online: bool


class SimulatorRunJob(BaseModel):
    test_case_id: int
    case_key: str
    title: str
    map_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    revision: int | None
    status: RunJobStatus
    verdict: RunVerdict | None = None
    collision: bool | None = None
    min_ttc_seconds: float | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SimulatorRunSummary(BaseModel):
    id: int
    status: SuiteRunStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    dispatched_at: datetime | None
    accepted_at: datetime | None
    requested_by: int
    requested_by_name: str | None
    bridge: SimulatorRunBridge | None
    total: int
    done: int
    passed: int
    failed: int
    # FAILED jobs (Bridge / CARLA error, timeout, missing result) and verdict ERROR.
    errors: int
    test_case_ids: list[int]


class SimulatorRunDetail(SimulatorRunSummary):
    jobs: list[SimulatorRunJob]
    # Requested test cases left out: (id, reason) — NOT_APPROVED, NOT_FOUND, NO_XOSC.
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class CaseRunEntry(BaseModel):
    run_id: int
    created_at: datetime
    requested_by_name: str | None
    job: SimulatorRunJob
