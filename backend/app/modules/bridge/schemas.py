from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
