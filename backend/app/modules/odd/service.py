"""ODD declaration storage, per-map capabilities (cached Agent map profile) and ODD-vs-CARLA gaps."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.options import build_options
from app.modules.catalog.service import visible_to
from app.modules.generation.agent_client import AgentPort
from app.modules.odd.schemas import MapCapability, OddDeclaration, OddGap
from app.shared.domain.errors import AgentUnavailable, GenerationRejected
from app.shared.infrastructure.models import CarlaCatalogSnapshot, OddProfile

# Agent map-profile road types -> ODD road categories.
ROAD_CATEGORIES = {
    "intersection_4way": "intersection", "intersection_3way": "intersection", "urban_straight": "straight",
    "urban_curve": "curve", "highway_straight": "highway", "highway_merge": "highway",
}


def road_categories(map_profile: dict) -> list[str]:
    found = {ROAD_CATEGORIES[road] for road in map_profile.get("road_types", []) if road in ROAD_CATEGORIES}
    return [category for category in ("intersection", "straight", "curve", "highway") if category in found]


async def get_odd(session: AsyncSession, project_id: int) -> OddProfile | None:
    return await session.get(OddProfile, project_id)


async def capabilities(session: AsyncSession, project_id: int, agent: AgentPort | None) -> list[MapCapability]:
    """One entry per visible snapshot. Map profiles are computed by the Agent once and cached on the row."""
    catalog = CarlaCatalogSnapshot.catalog
    rows = (await session.execute(
        select(CarlaCatalogSnapshot, catalog["vehicles"], catalog["walkers"])
        .where(visible_to(project_id))
        .order_by(CarlaCatalogSnapshot.created_at.desc(), CarlaCatalogSnapshot.id.desc())
    )).all()
    result: list[MapCapability] = []
    for snapshot, vehicles, walkers in rows:
        error = None
        if snapshot.map_profile is None and agent is not None:
            full = await session.scalar(select(CarlaCatalogSnapshot.catalog).where(CarlaCatalogSnapshot.id == snapshot.id))
            try:
                snapshot.map_profile = await agent.profile(catalog=full)
            except (AgentUnavailable, GenerationRejected) as exc:
                error = str(exc)
        if snapshot.map_profile is None and error is None:
            error = "Map profile not computed yet (Agent unavailable)"
        options = build_options([{
            "id": snapshot.id, "source": snapshot.source.value, "is_default": snapshot.project_id is None,
            "map_name": snapshot.map_name, "carla_version": snapshot.carla_version,
            "vehicles": vehicles, "walkers": walkers, "available_maps": [], "weather_presets": [],
        }])
        profile = snapshot.map_profile or {}
        result.append(MapCapability(
            snapshot_id=snapshot.id,
            map_name=snapshot.map_name,
            carla_version=snapshot.carla_version,
            origin="DEFAULT" if snapshot.project_id is None else snapshot.source.value,
            road_types=road_categories(profile),
            has_oncoming_lane=bool(profile.get("has_oncoming_lane")),
            adversary_types=[item["code"] for item in options["adversary_types"]],
            ego_vehicles=[item["code"] for item in options["ego_vehicles"]],
            error=error,
        ))
    await session.commit()  # persist freshly computed map profiles
    return result


def odd_gaps(declaration: OddDeclaration, caps: list[MapCapability]) -> list[OddGap]:
    """What the declared ODD asks for but the selected CARLA data cannot host."""
    by_id = {cap.snapshot_id: cap for cap in caps}
    gaps = [OddGap(dimension="catalog_snapshot_ids", value=str(item), reason="Dữ liệu CARLA không tồn tại hoặc không thuộc Project")
            for item in declaration.catalog_snapshot_ids if item not in by_id]
    selected = [by_id[item] for item in declaration.catalog_snapshot_ids if item in by_id]
    gaps += [OddGap(dimension="catalog_snapshot_ids", value=cap.map_name, reason=f"Chưa phân tích được map: {cap.error}")
             for cap in selected if cap.error]
    ready = [cap for cap in selected if not cap.error]
    for road in declaration.road_types:
        if not any(road in cap.road_types for cap in ready):
            gaps.append(OddGap(dimension="road_types", value=road, reason="Không map nào đã chọn có loại đường này trong dữ liệu làn"))
    for adversary in declaration.adversary_types:
        if not any(adversary in cap.adversary_types for cap in ready):
            gaps.append(OddGap(dimension="adversary_types", value=adversary, reason="Không có blueprint tương ứng trong dữ liệu CARLA đã chọn"))
    for ego in declaration.ego_vehicles:
        if not any(ego in cap.ego_vehicles for cap in ready):
            gaps.append(OddGap(dimension="ego_vehicles", value=ego, reason="Xe ego không có trong dữ liệu CARLA đã chọn"))
    return gaps
