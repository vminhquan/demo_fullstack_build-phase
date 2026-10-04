from __future__ import annotations

from app.modules.builder.schemas import BuilderMapOptions
from app.modules.builder.service import TARGET_COUNT, fallback_title, plan_variants


def test_always_plans_ten_variants_spread_over_the_maps() -> None:
    maps = [BuilderMapOptions(map_code="Town03"), BuilderMapOptions(map_code="Town04"), BuilderMapOptions(map_code="Town05")]
    variants = plan_variants(maps)
    assert TARGET_COUNT == 10
    assert [variant.no for variant in variants] == list(range(1, 11))
    assert [variant.map_code for variant in variants][:4] == ["Town03", "Town04", "Town05", "Town03"]
    assert sum(variant.map_code == "Town03" for variant in variants) == 4


def test_ticked_values_cycle_per_map_and_empty_categories_stay_with_the_agent() -> None:
    maps = [BuilderMapOptions(map_code="Town03", environment_codes=["rain", "fog", "clear"], danger_levels=["HIGH"]),
            BuilderMapOptions(map_code="Town04")]
    town03 = [variant for variant in plan_variants(maps) if variant.map_code == "Town03"]
    assert [variant.environment_code for variant in town03] == ["rain", "fog", "clear", "rain", "fog"]
    assert {variant.danger_level for variant in town03} == {"HIGH"}
    assert all(variant.ego_vehicle_code is None and variant.adversary_type is None for variant in town03)
    town04 = [variant for variant in plan_variants(maps) if variant.map_code == "Town04"]
    assert all(variant.environment_code is None and variant.danger_level is None for variant in town04)


def test_fallback_title_is_the_first_clause_in_a_few_words() -> None:
    assert fallback_title("xe máy tạt đầu ô tô ở ngã tư, ego 40 km/h") == "Xe máy tạt đầu ô tô ở ngã tư"
    assert len(fallback_title("một hai ba bốn năm sáu bảy tám chín mười mười một mười hai mười ba").split()) == 12
