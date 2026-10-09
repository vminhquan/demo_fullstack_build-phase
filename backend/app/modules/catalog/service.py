"""Catalog snapshots: the CARLA data scenarios are generated against.

`ingest_snapshot` is the single entry point for every source — the shipped defaults (seed),
an admin upload (IMPORT) and, once doc 18 is implemented, the Worker's `catalog.sync` (WORKER).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.modules.catalog.contract import CatalogV1
from app.shared.domain.errors import CatalogUnavailable, NotFound, ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.models import CarlaCatalogSnapshot, CatalogSource


# Fields added after catalog.v1 shipped: left out of the digest while absent, so older snapshots keep their hash.
LATE_CATALOG_FIELDS = (
    "weather_parameters", "road_speeds", "junctions", "landmarks", "traffic_lights", "crosswalks", "topology",
    "extraction_errors",
)
LATE_WAYPOINT_FIELDS = ("junction_id", "lane_change", "left_marking", "right_marking", "left_lane", "right_lane")
LATE_VEHICLE_FIELDS = ("length_m", "width_m", "height_m")
LATE_SITE_FIELDS = (
    "ego_s", "motorcycle_s", "s_direction", "target_side", "upstream_length_m", "lane_change_allowed_throughout",
    "marking_between", "max_heading_change_deg", "first_junction_m", "ends_at", "lane_width_m", "speed_limit_kmh",
)


def _drop_absent(item: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if item.get(key) is None:
            item.pop(key, None)


def content_hash(catalog: CatalogV1) -> str:
    """Stable digest of the catalog content (ignores any hash the sender put in the document).

    The OpenDRIVE text is covered by opendrive_hash, so it is not hashed twice."""
    canonical = catalog.model_dump(mode="json", exclude={"content_hash", "opendrive_xml"})
    _drop_absent(canonical, LATE_CATALOG_FIELDS)
    for waypoint in canonical["waypoints"]:
        _drop_absent(waypoint, LATE_WAYPOINT_FIELDS)
    for blueprint in canonical["vehicles"] + canonical["walkers"]:
        _drop_absent(blueprint, LATE_VEHICLE_FIELDS)
    # Preserve hashes of catalogs created before cut_in_sites was added to catalog.v1.
    if canonical["cut_in_sites"] is None:
        del canonical["cut_in_sites"]
    else:
        for site in canonical["cut_in_sites"]:
            _drop_absent(site, LATE_SITE_FIELDS)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_catalog(raw: dict[str, Any]) -> CatalogV1:
    try:
        catalog = CatalogV1.model_validate(raw)
    except ValueError as exc:
        raise ValidationFailed("Document is not a valid scenario-forge catalog (v1/v2)", {"errors": str(exc)[:2000]}) from exc
    if catalog.opendrive_xml is not None and catalog.opendrive_hash is not None:
        if hashlib.sha256(catalog.opendrive_xml.encode("utf-8")).hexdigest() != catalog.opendrive_hash:
            raise ValidationFailed("opendrive_xml does not match opendrive_hash")
    return catalog


async def ingest_snapshot(
    session: AsyncSession,
    catalog: CatalogV1,
    *,
    source: CatalogSource,
    project_id: int | None,
    actor_user_id: int | None = None,
    worker_installation_id: int | None = None,
    label: str | None = None,
) -> tuple[CarlaCatalogSnapshot, bool]:
    """Store a snapshot unless the same content already exists in that scope. Returns (snapshot, created).

    The OpenDRIVE text goes to its own deferred column: the JSON document is what the Agent receives."""
    if (source is CatalogSource.DEFAULT) != (project_id is None):
        raise ValidationFailed("DEFAULT snapshots are global; IMPORT/WORKER snapshots belong to a project")
    digest = content_hash(catalog)
    scope = CarlaCatalogSnapshot.project_id.is_(None) if project_id is None else CarlaCatalogSnapshot.project_id == project_id
    existing = await session.scalar(
        select(CarlaCatalogSnapshot).where(scope, CarlaCatalogSnapshot.content_hash == digest)
        .options(undefer(CarlaCatalogSnapshot.opendrive_xml))
    )
    if existing is not None:
        if existing.opendrive_xml is None and catalog.opendrive_xml is not None:
            existing.opendrive_xml = catalog.opendrive_xml
        return existing, False
    document = catalog.model_dump(mode="json", exclude_none=True, exclude={"opendrive_xml"})
    document["content_hash"] = digest
    snapshot = CarlaCatalogSnapshot(
        project_id=project_id,
        source=source,
        worker_installation_id=worker_installation_id,
        carla_version=catalog.carla_version,
        map_name=catalog.map_name,
        content_hash=digest,
        spawn_point_count=len(catalog.spawn_points),
        waypoint_count=len(catalog.waypoints),
        vehicle_count=len(catalog.vehicles),
        walker_count=len(catalog.walkers),
        catalog=document,
        opendrive_xml=catalog.opendrive_xml,
        label=label,
        created_by=actor_user_id,
    )
    session.add(snapshot)
    await session.flush()
    await record_audit(
        session,
        project_id=project_id,
        actor_user_id=actor_user_id,
        action="CATALOG_SNAPSHOT_INGESTED",
        entity_type="CARLA_CATALOG_SNAPSHOT",
        entity_id=snapshot.id,
        after_data={"source": source.value, "map_name": catalog.map_name, "carla_version": catalog.carla_version, "content_hash": digest},
    )
    return snapshot, True


def visible_to(project_id: int):
    """Projects see the shipped defaults plus their own snapshots."""
    return or_(CarlaCatalogSnapshot.project_id.is_(None), CarlaCatalogSnapshot.project_id == project_id)


async def get_snapshot(session: AsyncSession, project_id: int, snapshot_id: int, *, with_catalog: bool = False) -> CarlaCatalogSnapshot:
    statement = select(CarlaCatalogSnapshot).where(CarlaCatalogSnapshot.id == snapshot_id, visible_to(project_id))
    if with_catalog:
        statement = statement.options(undefer(CarlaCatalogSnapshot.catalog))
    snapshot = await session.scalar(statement)
    if snapshot is None:
        raise NotFound("CARLA catalog snapshot not found")
    return snapshot


async def resolve_for_generation(
    session: AsyncSession,
    project_id: int,
    *,
    snapshot_id: int | None,
    source: str,
    map_name: str | None,
) -> CarlaCatalogSnapshot:
    """Explicit snapshot wins; otherwise the newest DEFAULT or project (IMPORT/WORKER) snapshot for the map."""
    if snapshot_id is not None:
        return await get_snapshot(session, project_id, snapshot_id, with_catalog=True)
    if source == "DEFAULT":
        statement = select(CarlaCatalogSnapshot).where(CarlaCatalogSnapshot.source == CatalogSource.DEFAULT)
    else:
        statement = select(CarlaCatalogSnapshot).where(
            CarlaCatalogSnapshot.project_id == project_id,
            CarlaCatalogSnapshot.source.in_([CatalogSource.WORKER, CatalogSource.IMPORT]),
        )
    if map_name:
        statement = statement.where(CarlaCatalogSnapshot.map_name == map_name)
    snapshot = await session.scalar(
        statement.options(undefer(CarlaCatalogSnapshot.catalog)).order_by(CarlaCatalogSnapshot.created_at.desc(), CarlaCatalogSnapshot.id.desc()).limit(1)
    )
    if snapshot is None:
        if source == "DEFAULT":
            raise CatalogUnavailable("No default CARLA data is installed; run `python -m app.seed_catalog`", "CATALOG_NOT_INSTALLED")
        raise CatalogUnavailable("This project has no CARLA data from the user's machine yet; sync it with the Worker or import a catalog")
    return snapshot
