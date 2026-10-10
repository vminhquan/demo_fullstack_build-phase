from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.builder.schemas import BuilderMapOptions, BuilderSessionCreate
from app.modules.builder.service import TARGET_COUNT, fallback_title, plan_variants


def test_always_plans_ten_variants_spread_over_the_maps() -> None:
    maps = [BuilderMapOptions(map_code="Town03"), BuilderMapOptions(map_code="Town04"), BuilderMapOptions(map_code="Town05")]
    variants = plan_variants(maps)
    assert TARGET_COUNT == 10
    assert [variant.no for variant in variants] == list(range(1, 11))
    assert [variant.map_code for variant in variants][:4] == ["Town03", "Town04", "Town05", "Town03"]
    assert sum(variant.map_code == "Town03" for variant in variants) == 4


def test_ticked_values_cycle_by_variant_and_empty_categories_stay_with_the_agent() -> None:
    maps = [BuilderMapOptions(map_code="Town03", environment_codes=["rain", "fog", "clear"], danger_levels=["HIGH"]),
            BuilderMapOptions(map_code="Town04")]
    town03 = [variant for variant in plan_variants(maps) if variant.map_code == "Town03"]  # variants 1, 3, 5, 7, 9
    assert [variant.environment_code for variant in town03] == ["rain", "clear", "fog", "rain", "clear"]
    assert {variant.danger_level for variant in town03} == {"HIGH"}
    assert all(variant.ego_vehicle_code is None and variant.adversary_type is None for variant in town03)
    town04 = [variant for variant in plan_variants(maps) if variant.map_code == "Town04"]
    assert all(variant.environment_code is None and variant.danger_level is None for variant in town04)


def test_fallback_title_is_the_first_clause_in_a_few_words() -> None:
    assert fallback_title("xe máy tạt đầu ô tô ở ngã tư, ego 40 km/h") == "Xe máy tạt đầu ô tô ở ngã tư"
    assert len(fallback_title("một hai ba bốn năm sáu bảy tám chín mười mười một mười hai mười ba").split()) == 12


def test_session_requires_an_ego_for_every_picked_map() -> None:
    with pytest.raises(ValidationError, match="Town04"):
        BuilderSessionCreate(description="Xe máy tạt đầu", maps=[
            BuilderMapOptions(map_code="Town03", ego_vehicle_codes=["vehicle.tesla.model3"]),
            BuilderMapOptions(map_code="Town04"),
        ])
    body = BuilderSessionCreate(description="Xe máy tạt đầu", maps=[
        BuilderMapOptions(map_code="Town03", ego_vehicle_codes=["vehicle.tesla.model3"]),
    ])
    assert body.maps[0].ego_vehicle_codes == ["vehicle.tesla.model3"]


def test_variants_keep_the_snapshot_the_screen_listed() -> None:
    maps = [BuilderMapOptions(map_code="Town03", catalog_snapshot_id=7, ego_vehicle_codes=["vehicle.tesla.model3"])]
    assert {variant.catalog_snapshot_id for variant in plan_variants(maps)} == {7}


async def test_variants_leave_maps_that_cannot_host_the_description(monkeypatch) -> None:
    from app.modules.builder import service

    maps = [BuilderMapOptions(map_code=code, ego_vehicle_codes=["vehicle.audi.a2"]) for code in ("Town01", "Town03", "Town04")]
    unfit = {"Town01": "NO_VERIFIED_SITES", "Town04": "LOCATION_NOT_AVAILABLE"}
    tried, recorded = [], []

    async def fake_run(builder_id, builder, actor, agent, variant, *, record_failure=True):
        tried.append((variant.no, variant.map_code))
        if variant.map_code in unfit:
            return {"variant_no": variant.no, "map_code": variant.map_code, "code": unfit[variant.map_code], "message": "no 4-way"}
        return None

    async def fake_record(builder_id, error):
        recorded.append(error)

    monkeypatch.setattr(service, "run_variant", fake_run)
    monkeypatch.setattr(service, "record_error", fake_record)
    routing = service.MapRouting(maps)
    for variant in plan_variants(maps):
        await service.run_routed(1, None, None, None, variant, routing)
    assert recorded == []  # every variant ended on Town03
    assert sorted(routing.unfit) == ["Town01", "Town04"]
    landed = {}
    for no, code in tried:
        landed[no] = code
    assert set(landed.values()) == {"Town03"} and len(landed) == TARGET_COUNT
    # Once a map is known not to fit, later variants skip it without calling the Agent.
    assert sum(code == "Town04" for _, code in tried) == 1 and sum(code == "Town01" for _, code in tried) == 1


async def test_no_fitting_map_is_one_clear_error_and_other_failures_are_kept(monkeypatch) -> None:
    from app.modules.builder import service

    maps = [BuilderMapOptions(map_code="Town01"), BuilderMapOptions(map_code="Town02")]
    recorded = []

    async def unfit_everywhere(builder_id, builder, actor, agent, variant, *, record_failure=True):
        return {"variant_no": variant.no, "map_code": variant.map_code, "code": "NO_VERIFIED_SITES", "message": "no site"}

    async def fake_record(builder_id, error):
        recorded.append(error)

    monkeypatch.setattr(service, "run_variant", unfit_everywhere)
    monkeypatch.setattr(service, "record_error", fake_record)
    await service.run_routed(1, None, None, None, plan_variants(maps)[0], service.MapRouting(maps))
    assert [item["code"] for item in recorded] == ["NO_FITTING_MAP"] and "Town02: no site" in recorded[0]["message"]

    recorded.clear()

    async def agent_down(builder_id, builder, actor, agent, variant, *, record_failure=True):
        return {"variant_no": variant.no, "map_code": variant.map_code, "code": "AGENT_UNAVAILABLE", "message": "down"}

    monkeypatch.setattr(service, "run_variant", agent_down)
    await service.run_routed(1, None, None, None, plan_variants(maps)[0], service.MapRouting(maps))
    assert [item["code"] for item in recorded] == ["AGENT_UNAVAILABLE"]


def test_many_maps_still_cover_every_ticked_environment() -> None:
    ticked = ["clear", "rain", "heavy_rain", "fog", "night", "dusk", "HardRainNight", "ClearNoon", "WetSunset", "DustStorm"]
    maps = [BuilderMapOptions(map_code=f"Town{index:02d}", environment_codes=ticked) for index in range(12)]
    # One variant per map: before, every one took the first ticked value ("clear").
    assert [variant.environment_code for variant in plan_variants(maps)] == ticked
