"""Internal endpoints for agent2 (Tầng A: Agent → Backend HTTP → DB).

Reads answer agent2's tool calls from the light JSON keys of a stored snapshot, never the waypoints; each one
loads only the keys its answer needs. The save endpoint is agent2's last graph node: it stores the validated XOSC
as a ScenarioGeneration for the project and user the Backend named in the request. Authenticated with the Agent
key the Backend already shares with agent2.
"""
from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.agent_tools import (
    MAX_SITES, SECTION_KEYS, Section, catalog_summary, list_vehicles, list_weather_presets, search_sites, site_section,
)
from app.modules.generation.agent_client import scenario_result
from app.shared.config import get_settings
from app.shared.domain.errors import NotFound, Unauthorized, ValidationFailed
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import CarlaCatalogSnapshot, GenerationStatus, ScenarioGeneration

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"], include_in_schema=False)


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


@router.get("/catalog/snapshots/{snapshot_id}/summary", dependencies=[Depends(require_agent)])
async def read_summary(snapshot_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return catalog_summary(await _document(session, snapshot_id, ("cut_in_sites",)))


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
    seed: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    document = await _document(session, snapshot_id, ("cut_in_sites",))
    return search_sites(
        document, tags=tags, min_length_m=min_length_m, speed_kmh_min=speed_kmh_min, speed_kmh_max=speed_kmh_max,
        target_side=target_side, lane_change_allowed=lane_change_allowed, near_junction=near_junction, limit=limit,
        seed=seed,
    )


@router.get("/catalog/snapshots/{snapshot_id}/cut-in-sites/{site_id}/{section}", dependencies=[Depends(require_agent)])
async def read_site_section(
    snapshot_id: int, site_id: str, section: Section, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    document = await _document(session, snapshot_id, ("cut_in_sites", *SECTION_KEYS[section]))
    result = site_section(document, site_id, section)
    if result is None:
        raise NotFound("Cut-in site not found in this snapshot")
    return result


@router.get("/catalog/snapshots/{snapshot_id}/vehicles", dependencies=[Depends(require_agent)])
async def read_vehicles(
    snapshot_id: int, kind: Literal["all", "car", "motorcycle"], seed: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return list_vehicles(await _document(session, snapshot_id, ("vehicles",)), kind, seed=seed)


@router.get("/catalog/snapshots/{snapshot_id}/weather-presets", dependencies=[Depends(require_agent)])
async def read_weather_presets(snapshot_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return list_weather_presets(await _document(session, snapshot_id, ("weather_presets",)))


class SaveTarget(BaseModel):
    """Who the generation belongs to; set by the Backend when it called agent2, passed back unchanged."""

    project_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    prompt: str = Field(min_length=1)
    request_hash: str | None = None


class SavedScenario(BaseModel):
    save_to: SaveTarget
    snapshot_id: int = Field(gt=0)
    model_name: str | None = None
    # agent2's GeneratedScenario: snapshot, site_id, plan, validation, xosc, xosc_sha256...
    scenario: dict[str, Any]


@router.post("/scenario-generations", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_agent)])
async def save_scenario(body: SavedScenario, session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    document = await _document(session, body.snapshot_id, ("cut_in_sites",))
    scenario = body.scenario
    if (scenario.get("snapshot") or {}).get("snapshot_id") != body.snapshot_id:
        raise ValidationFailed("The scenario was generated on another CARLA snapshot")
    if not scenario.get("xosc") or not scenario.get("xosc_sha256"):
        raise ValidationFailed("The scenario has no XOSC")
    result = scenario_result(scenario, document, body.model_name)
    xosc = result.pop("xosc")
    generation = ScenarioGeneration(
        request_hash=body.save_to.request_hash,
        cache_hit=False,
        project_id=body.save_to.project_id,
        created_by=body.save_to.user_id,
        prompt=body.save_to.prompt,
        catalog_snapshot_id=body.snapshot_id,
        status=GenerationStatus.COMPLETED,
        generation_mode=result["generation_mode"],
        model=body.model_name,
        result=result,
        xosc=xosc,
        xosc_sha256=result["xosc_sha256"],
    )
    session.add(generation)
    await session.commit()
    return {"generation_id": generation.id}
