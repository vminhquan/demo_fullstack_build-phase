from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.importers import from_upload
from app.modules.catalog.options import build_options
from app.modules.catalog.schemas import CatalogImportRequest, CatalogImportResponse, CatalogSnapshotResponse, MetadataOptionsResponse
from app.modules.catalog.service import get_snapshot, ingest_snapshot, parse_catalog, visible_to
from app.modules.identity.dependencies import Principal, require
from app.shared.config import get_settings
from app.shared.domain.errors import ValidationFailed
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import CarlaCatalogSnapshot, CatalogSource

router = APIRouter(prefix="/carla-catalog", tags=["carla-catalog"])


@router.get("/snapshots", response_model=list[CatalogSnapshotResponse])
async def list_snapshots(
    source: Literal["DEFAULT", "PROJECT"] | None = None,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> list[CatalogSnapshotResponse]:
    statement = select(CarlaCatalogSnapshot).where(visible_to(actor.project_id))
    if source == "DEFAULT":
        statement = statement.where(CarlaCatalogSnapshot.project_id.is_(None))
    elif source == "PROJECT":
        statement = statement.where(CarlaCatalogSnapshot.project_id == actor.project_id)
    rows = (await session.scalars(statement.order_by(CarlaCatalogSnapshot.created_at.desc(), CarlaCatalogSnapshot.id.desc()))).all()
    return [CatalogSnapshotResponse.from_model(row) for row in rows]


@router.get("/metadata-options", response_model=MetadataOptionsResponse)
async def metadata_options(
    snapshot_id: int | None = None,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> MetadataOptionsResponse:
    """Choices for the 5 version metadata fields, computed from the CARLA catalogs this project can see
    (or from one snapshot). Only the light catalog keys are read — never the waypoints."""
    catalog = CarlaCatalogSnapshot.catalog
    statement = (
        select(
            CarlaCatalogSnapshot,
            catalog["vehicles"],
            catalog["walkers"],
            catalog["available_maps"],
            catalog["weather_presets"],
        )
        .where(visible_to(actor.project_id))
        .order_by(CarlaCatalogSnapshot.created_at.desc(), CarlaCatalogSnapshot.id.desc())
    )
    if snapshot_id is not None:
        statement = statement.where(CarlaCatalogSnapshot.id == snapshot_id)
    rows = (await session.execute(statement)).all()
    options = build_options([
        {
            "id": snapshot.id,
            "source": snapshot.source.value,
            "is_default": snapshot.project_id is None,
            "map_name": snapshot.map_name,
            "carla_version": snapshot.carla_version,
            "vehicles": vehicles,
            "walkers": walkers,
            "available_maps": available_maps,
            "weather_presets": weather_presets,
        }
        for snapshot, vehicles, walkers, available_maps, weather_presets in rows
    ])
    return MetadataOptionsResponse(snapshots=[CatalogSnapshotResponse.from_model(row[0]) for row in rows], **options)


@router.get("/snapshots/{snapshot_id}", response_model=CatalogSnapshotResponse)
async def read_snapshot(
    snapshot_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> CatalogSnapshotResponse:
    return CatalogSnapshotResponse.from_model(await get_snapshot(session, actor.project_id, snapshot_id))


@router.post("/snapshots/import", response_model=CatalogImportResponse, status_code=status.HTTP_201_CREATED)
async def import_snapshot(
    body: CatalogImportRequest,
    actor: Principal = Depends(require("catalog:import")),
    session: AsyncSession = Depends(get_session),
) -> CatalogImportResponse:
    """Upload of the user's CARLA data as catalog.v1 JSON — the manual stand-in for the Worker's catalog.sync."""
    snapshot, created = await ingest_snapshot(
        session,
        parse_catalog(body.catalog),
        source=CatalogSource.IMPORT,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        label=body.label,
    )
    await session.commit()
    return CatalogImportResponse(snapshot=CatalogSnapshotResponse.from_model(snapshot), created=created)


@router.post("/snapshots/upload", response_model=CatalogImportResponse, status_code=status.HTTP_201_CREATED)
async def upload_snapshot(
    file: UploadFile = File(...),
    label: str | None = Form(default=None, max_length=200),
    actor: Principal = Depends(require("catalog:import")),
    session: AsyncSession = Depends(get_session),
) -> CatalogImportResponse:
    """User-friendly import: the zipped CARLA export folder, a catalog.v1 .json or a worker context .json."""
    limit = get_settings().max_catalog_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ValidationFailed(f"File exceeds {limit // (1024 * 1024)} MB")
    snapshot, created = await ingest_snapshot(
        session,
        from_upload(file.filename or "upload", content),
        source=CatalogSource.IMPORT,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        label=label or None,
    )
    await session.commit()
    return CatalogImportResponse(snapshot=CatalogSnapshotResponse.from_model(snapshot), created=created)
