"""Read-only catalog endpoints for agent2's tool calls (Tầng A: Agent → Backend HTTP → stored snapshot).

Authenticated with the Agent key the Backend already shares with agent2. They read only the light JSON keys
of a snapshot, never the waypoints.
"""
from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.agent_tools import MAX_SITES, search_sites, site_context
from app.shared.config import get_settings
from app.shared.domain.errors import NotFound, Unauthorized
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import CarlaCatalogSnapshot

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"], include_in_schema=False)

CONTEXT_KEYS = ("cut_in_sites", "road_speeds", "landmarks", "traffic_lights", "crosswalks", "junctions")


def require_agent(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.agent_api_key:
        if settings.app_env in {"development", "test"}:
            return
        raise Unauthorized("Agent key is not configured")
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.agent_api_key):
        raise Unauthorized("Invalid Agent API key")


async def _document(session: AsyncSession, snapshot_id: int, keys: tuple[str, ...]) -> dict[str, Any]:
    catalog = CarlaCatalogSnapshot.catalog
    row = (await session.execute(
        select(CarlaCatalogSnapshot.map_name, *(catalog[key] for key in keys)).where(CarlaCatalogSnapshot.id == snapshot_id)
    )).first()
    if row is None:
        raise NotFound("CARLA catalog snapshot not found")
    return {"map_name": row[0], **dict(zip(keys, row[1:]))}


@router.get("/catalog/snapshots/{snapshot_id}/cut-in-sites", dependencies=[Depends(require_agent)])
async def find_sites(
    snapshot_id: int,
    tags: list[str] = Query(default_factory=list),
    min_length_m: float | None = None,
    speed_kmh_min: float | None = None,
    speed_kmh_max: float | None = None,
    target_side: Literal["left", "right"] | None = None,
    lane_change_allowed: bool | None = None,
    near_junction: bool | None = None,
    limit: int = Query(default=10, ge=1, le=MAX_SITES),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    document = await _document(session, snapshot_id, ("cut_in_sites",))
    return search_sites(
        document, tags=tags, min_length_m=min_length_m, speed_kmh_min=speed_kmh_min, speed_kmh_max=speed_kmh_max,
        target_side=target_side, lane_change_allowed=lane_change_allowed, near_junction=near_junction, limit=limit,
    )


@router.get("/catalog/snapshots/{snapshot_id}/cut-in-sites/{site_id}/context", dependencies=[Depends(require_agent)])
async def read_site_context(snapshot_id: int, site_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    context = site_context(await _document(session, snapshot_id, CONTEXT_KEYS), site_id)
    if context is None:
        raise NotFound("Cut-in site not found in this snapshot")
    return context
