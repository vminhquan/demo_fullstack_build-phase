"""Catalog snapshots: the CARLA data scenarios are generated against.

`ingest_snapshot` is the single entry point for every source — the shipped defaults (seed),
an admin upload (IMPORT) and, once doc 18 is implemented, the Worker's `catalog.sync` (WORKER).
"""
from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.catalog.contract import CatalogV1
from app.shared.domain.errors import CatalogUnavailable, NotFound, ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.models import CarlaCatalogSnapshot, CarlaMapData, CatalogSource


# Fields added after catalog.v1 shipped: left out of the digest while absent, so older snapshots keep their hash.
LATE_CATALOG_FIELDS = (
    "weather_parameters", "road_speeds", "junctions", "landmarks", "traffic_lights", "crosswalks", "topology",
    "extraction_errors",
)
LATE_WAYPOINT_FIELDS = ("junction_id", "lane_change", "left_marking", "right_marking", "left_lane", "right_lane")
LATE_VEHICLE_FIELDS = ("length_m", "width_m", "height_m")
# CARLA hands out new actor ids every time a map is loaded: hashing them made each sync a "new" snapshot.
RUNTIME_TRAFFIC_LIGHT_FIELDS = ("actor_id", "group_actor_ids")
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
    for light in canonical.get("traffic_lights") or []:
        for key in RUNTIME_TRAFFIC_LIGHT_FIELDS:
            light.pop(key, None)
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


def pack_map_data(waypoints: list[dict[str, Any]], opendrive_hash: str | None, opendrive_xml: str | None) -> dict[str, Any]:
    """Row values of carla_map_data. The hash covers what makes the map: its waypoints and its OpenDRIVE."""
    packed = json.dumps(waypoints, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(packed + b"\n" + (opendrive_hash or "").encode()).hexdigest()
    return {
        "data_hash": digest,
        "waypoints_gz": gzip.compress(packed, compresslevel=6),
        "opendrive_gz": gzip.compress(opendrive_xml.encode("utf-8"), compresslevel=6) if opendrive_xml is not None else None,
    }


async def store_map_data(session: AsyncSession, values: dict[str, Any]) -> int:
    """Id of the carla_map_data row holding these values, inserting it unless the same map is already stored."""
    await session.execute(insert(CarlaMapData).values(**values).on_conflict_do_nothing(index_elements=["data_hash"]))
    stored = await session.scalar(
        select(CarlaMapData).where(CarlaMapData.data_hash == values["data_hash"]).options(undefer(CarlaMapData.opendrive_gz))
    )
    if stored.opendrive_gz is None and values["opendrive_gz"] is not None:
        stored.opendrive_gz = values["opendrive_gz"]
    return stored.id


async def with_waypoints(session: AsyncSession, document: dict[str, Any], map_data_id: int | None) -> dict[str, Any]:
    """The whole catalog document: a stored one without waypoints gets them back from carla_map_data."""
    if map_data_id is None or "waypoints" in document:
        return document
    packed = await session.scalar(select(CarlaMapData.waypoints_gz).where(CarlaMapData.id == map_data_id))
    return {**document, "waypoints": json.loads(gzip.decompress(packed))}


async def attach_waypoints(session: AsyncSession, snapshot: CarlaCatalogSnapshot) -> CarlaCatalogSnapshot:
    """Put the waypoints back into a loaded `snapshot.catalog`, so readers see the whole document.

    Set as the committed value: the row is never rewritten with the waypoints inline."""
    set_committed_value(snapshot, "catalog", await with_waypoints(session, snapshot.catalog, snapshot.map_data_id))
    return snapshot


async def load_opendrive(session: AsyncSession, snapshot: CarlaCatalogSnapshot) -> str | None:
    """OpenDRIVE of a snapshot, wherever it was stored."""
    if snapshot.map_data_id is not None:
        packed = await session.scalar(select(CarlaMapData.opendrive_gz).where(CarlaMapData.id == snapshot.map_data_id))
        return gzip.decompress(packed).decode("utf-8") if packed is not None else None
    return await session.scalar(select(CarlaCatalogSnapshot.opendrive_xml).where(CarlaCatalogSnapshot.id == snapshot.id))


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

    Waypoints and OpenDRIVE go to the shared carla_map_data; the snapshot row keeps the light JSON."""
    if (source is CatalogSource.DEFAULT) != (project_id is None):
        raise ValidationFailed("DEFAULT snapshots are global; IMPORT/WORKER snapshots belong to a project")
    digest = content_hash(catalog)
    scope = CarlaCatalogSnapshot.project_id.is_(None) if project_id is None else CarlaCatalogSnapshot.project_id == project_id
    existing = await session.scalar(select(CarlaCatalogSnapshot).where(scope, CarlaCatalogSnapshot.content_hash == digest))
    if existing is not None and (existing.map_data_id is None or catalog.opendrive_xml is None):
        return existing, False
    document = catalog.model_dump(mode="json", exclude_none=True, exclude={"opendrive_xml"})
    map_data = pack_map_data(document.pop("waypoints"), catalog.opendrive_hash, catalog.opendrive_xml)
    if existing is not None:
        # Same content again, now with the OpenDRIVE an earlier upload may not have carried.
        await store_map_data(session, map_data)
        return existing, False
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
        map_data_id=await store_map_data(session, map_data),
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
    return await attach_waypoints(session, snapshot) if with_catalog else snapshot


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
    return await attach_waypoints(session, snapshot)
