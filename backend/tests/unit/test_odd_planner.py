import pytest

from app.modules.odd.planner import plan_cases
from app.modules.odd.schemas import MapCapability, NumberRange, OddDeclaration, PlanForm
from app.modules.odd.service import odd_gaps, road_categories
from app.shared.domain.errors import ValidationFailed

TOWN10 = MapCapability(snapshot_id=1, map_name="Town10HD_Opt", carla_version="0.9.16", origin="DEFAULT",
                       road_types=["intersection", "straight", "curve"], has_oncoming_lane=True,
                       adversary_types=["pedestrian", "motorcycle", "car"], ego_vehicles=["vehicle.tesla.model3"])
TOWN04 = MapCapability(snapshot_id=2, map_name="Town04", carla_version="0.9.16", origin="IMPORT",
                       road_types=["straight", "highway"], has_oncoming_lane=False,
                       adversary_types=["motorcycle", "car", "truck"], ego_vehicles=["vehicle.tesla.model3", "vehicle.audi.a2"])


def form(**overrides) -> PlanForm:
    base = {
        "scenario_type": "cut_in", "road_types": ["intersection", "highway"], "weather": ["clear", "rain"],
        "lighting": ["day", "night"], "adversary_types": ["motorcycle", "pedestrian"],
        "ego_speed_kmh": {"min": 20, "max": 60}, "actor_speed_kmh": {"min": 10, "max": 40},
        "initial_gap_m": {"min": 10, "max": 30}, "trigger_distance_m": {"min": 5, "max": 15},
    }
    base.update(overrides)
    return PlanForm(**base)


def test_road_categories_from_map_profile() -> None:
    assert road_categories({"road_types": ["urban_straight", "intersection_3way", "highway_merge"]}) == ["intersection", "straight", "highway"]


def test_plan_covers_all_feasible_pairs_and_picks_hosting_maps() -> None:
    result = plan_cases(form(), 8, [TOWN10, TOWN04], None)
    assert result.pairs_covered == result.pairs_total and not result.uncovered_pairs
    for case in result.cases:
        cap = TOWN10 if case.catalog_snapshot_id == 1 else TOWN04
        assert case.cell["road_type"] in cap.road_types and case.cell["adversary_type"] in cap.adversary_types
    # highway x pedestrian exists on no map: reported, never planned
    assert any(gap.value == "highway × pedestrian" for gap in result.infeasible)
    assert all(not (c.cell["road_type"] == "highway" and c.cell["adversary_type"] == "pedestrian") for c in result.cases)


def test_ranges_are_split_into_distinct_strata() -> None:
    result = plan_cases(form(), 4, [TOWN10, TOWN04], None)
    ego = sorted((case.spec["ego_speed_kmh"]["min"], case.spec["ego_speed_kmh"]["max"]) for case in result.cases)
    assert ego == [(20.0, 30.0), (30.0, 40.0), (40.0, 50.0), (50.0, 60.0)]
    assert all(case.spec["weather"] in (["clear"], ["rain"]) for case in result.cases)
    # gap and trigger slices move together: trigger slice i always sits in the same position as gap slice i
    for case in result.cases:
        gap, trigger = case.spec["initial_gap_m"], case.spec["trigger_distance_m"]
        assert (gap["min"] - 10) / 5 == (trigger["min"] - 5) / 2.5


def test_values_outside_odd_are_dropped_and_ranges_clipped() -> None:
    odd = OddDeclaration(catalog_snapshot_ids=[1], road_types=["intersection"], weather=["rain"], lighting=["day"],
                         adversary_types=["motorcycle"], ego_vehicles=["vehicle.tesla.model3"],
                         ego_speed_kmh=NumberRange(min=20, max=40), actor_speed_kmh=NumberRange(min=0, max=50))
    result = plan_cases(form(), 2, [TOWN10, TOWN04], odd)
    assert {case.catalog_snapshot_id for case in result.cases} == {1}
    assert all(case.cell == {"road_type": "intersection", "weather": "rain", "lighting": "day", "adversary_type": "motorcycle"} for case in result.cases)
    assert max(case.spec["ego_speed_kmh"]["max"] for case in result.cases) == 40
    assert {gap.value for gap in result.outside_odd} >= {"highway", "clear", "night", "pedestrian"}
    assert all(case.spec["ego_blueprint"] == "vehicle.tesla.model3" for case in result.cases)


def test_nothing_feasible_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        plan_cases(form(road_types=["highway"], adversary_types=["pedestrian"]), 2, [TOWN10, TOWN04], None)


def test_odd_gaps_report_what_carla_data_cannot_host() -> None:
    odd = OddDeclaration(catalog_snapshot_ids=[1], road_types=["intersection", "highway"], weather=["rain"], lighting=["day"],
                         adversary_types=["motorcycle", "truck"], ego_vehicles=["vehicle.audi.a2"])
    gaps = {(gap.dimension, gap.value) for gap in odd_gaps(odd, [TOWN10, TOWN04])}
    assert gaps == {("road_types", "highway"), ("adversary_types", "truck"), ("ego_vehicles", "vehicle.audi.a2")}


def test_auto_count_is_the_fewest_cases_covering_all_pairs() -> None:
    result = plan_cases(form(), None, [TOWN10, TOWN04], None)
    assert result.auto and result.pairs_covered == result.pairs_total
    fewer = plan_cases(form(), len(result.cases) - 1, [TOWN10, TOWN04], None)
    assert fewer.pairs_covered < fewer.pairs_total
