from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.shared.infrastructure.models import CarlaCatalogSnapshot, CatalogSource


class CatalogSnapshotResponse(BaseModel):
    id: int
    source: CatalogSource
    is_default: bool
    carla_version: str
    map_name: str
    content_hash: str
    spawn_point_count: int
    waypoint_count: int
    vehicle_count: int
    walker_count: int
    label: str | None
    worker_installation_id: int | None
    created_by: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, snapshot: CarlaCatalogSnapshot) -> "CatalogSnapshotResponse":
        return cls(
            id=snapshot.id,
            source=snapshot.source,
            is_default=snapshot.project_id is None,
            carla_version=snapshot.carla_version,
            map_name=snapshot.map_name,
            content_hash=snapshot.content_hash,
            spawn_point_count=snapshot.spawn_point_count,
            waypoint_count=snapshot.waypoint_count,
            vehicle_count=snapshot.vehicle_count,
            walker_count=snapshot.walker_count,
            label=snapshot.label,
            worker_installation_id=snapshot.worker_installation_id,
            created_by=snapshot.created_by,
            created_at=snapshot.created_at,
        )


class CatalogImportRequest(BaseModel):
    # A full catalog.v1 document (see docs/19-agent-sinh-kich-ban.md).
    catalog: dict[str, Any]
    label: str | None = Field(default=None, max_length=200)


class CatalogImportResponse(BaseModel):
    snapshot: CatalogSnapshotResponse
    created: bool


class MapOption(BaseModel):
    code: str
    # True when lane data exists (the Agent can generate on it); False = listed by a CARLA server only.
    has_lane_data: bool
    snapshot_ids: list[int]
    sources: list[str]
    carla_versions: list[str]


class VehicleOption(BaseModel):
    code: str
    label: str
    base_type: str
    snapshot_ids: list[int]


class CodeOption(BaseModel):
    code: str
    label: str
    snapshot_ids: list[int]
    group: str | None = None


class MetadataOptionsResponse(BaseModel):
    snapshots: list[CatalogSnapshotResponse]
    maps: list[MapOption]
    ego_vehicles: list[VehicleOption]
    adversary_types: list[CodeOption]
    environments: list[CodeOption]
