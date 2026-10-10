from __future__ import annotations

from app.cut_in.model import CutInPlan
from app.cut_in.sample import SampledVariant
from app.cut_in.validate import repair_maneuver, validate_cut_in
from test_llm import variant
from test_sample import snapshot


def plan(**changes) -> CutInPlan:
    sampled = variant()
    values = {
        **sampled.context.model_dump(),
        "motorcycle_start_offset_m": 10,
        "trigger_time_s": 0.5,
        "lane_change_duration_s": 2.0,
        "desired_lead_gap_m": 5,
        **changes,
    }
    return CutInPlan.model_validate(values)


def codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_accepts_grounded_plan_with_feasible_constant_speed_estimate() -> None:
    sampled = variant()
    result = validate_cut_in(plan(), sampled, snapshot("Town01", 1))
    assert result.valid and result.issues == []


def test_rejects_negative_start_offset_without_verified_upstream_road() -> None:
    result = validate_cut_in(plan(motorcycle_start_offset_m=-5), variant(), snapshot("Town01", 1))
    assert not result.valid
    issue = next(item for item in result.issues if item.code == "START_OUTSIDE_CORRIDOR")
    assert issue.recoverable and issue.field == "motorcycle_start_offset_m"


def test_rejects_travel_beyond_verified_corridor_and_insufficient_gap() -> None:
    too_late = validate_cut_in(plan(trigger_time_s=8), variant(), snapshot("Town01", 1))
    assert "TRAVEL_OUTSIDE_CORRIDOR" in codes(too_late)
    no_lead = validate_cut_in(plan(motorcycle_start_offset_m=0, desired_lead_gap_m=20), variant(), snapshot("Town01", 1))
    assert "INSUFFICIENT_LEAD_GAP" in codes(no_lead)


def test_rejects_too_fast_lane_change() -> None:
    result = validate_cut_in(plan(lane_change_duration_s=0.5), variant(), snapshot("Town01", 1))
    assert "LANE_CHANGE_TOO_FAST" in codes(result)


def test_rejects_changed_locked_speed_and_wrong_snapshot() -> None:
    changed = validate_cut_in(plan(ego_speed_kmh=60), variant(), snapshot("Town01", 1))
    assert "CONTEXT_CHANGED" in codes(changed)
    wrong_map = validate_cut_in(plan(), variant(), snapshot("Town03", 2))
    assert {"SNAPSHOT_MISMATCH", "SITE_NOT_IN_CATALOG"}.issubset(codes(wrong_map))


def test_rejects_site_changed_after_sync() -> None:
    sampled = variant()
    forged_site = sampled.site.model_copy(update={"available_length_m": 100})
    result = validate_cut_in(plan(), SampledVariant(site=forged_site, context=sampled.context), snapshot("Town01", 1))
    assert "SITE_NOT_IN_CATALOG" in codes(result)


def test_rejects_weather_not_in_catalog() -> None:
    selected = snapshot("Town01", 1, presets=["ClearNoon"])
    result = validate_cut_in(plan(), variant(), selected)
    assert "WEATHER_NOT_IN_CATALOG" in codes(result)


def test_rejects_manual_site_with_opposite_lane_heading() -> None:
    selected = snapshot("Town01", 1)
    selected.catalog.cut_in_sites[0].motorcycle_anchor.yaw = 180
    sampled = variant()
    sampled.site.motorcycle_anchor.yaw = 180
    result = validate_cut_in(plan(), sampled, selected)
    assert "INVALID_LANE_GEOMETRY" in codes(result)


def test_rejects_non_finite_offset() -> None:
    result = validate_cut_in(plan(motorcycle_start_offset_m=float("inf")), variant(), snapshot("Town01", 1))
    assert "NON_FINITE_PARAMETER" in codes(result)
    assert result.issues[0].recoverable


def test_repair_finds_passing_numbers_or_reports_that_none_fit() -> None:
    sampled = variant()
    selected = snapshot("Town01", 1)
    base = {**sampled.context.model_dump(), "trigger_time_s": 3.0, "lane_change_duration_s": 0.5, "desired_lead_gap_m": 8}
    # Motorcycle behind and too slow a lane change: offset, duration (and trigger if needed) are moved just enough.
    repaired = repair_maneuver(CutInPlan.model_validate({**base, "motorcycle_start_offset_m": -5}), sampled)
    assert repaired is not None and validate_cut_in(repaired, sampled, selected).valid
    assert repaired.lane_change_duration_s >= 1.6 and repaired.site_id == sampled.context.site_id
    # 250 km/h leaves the 65 m corridor before any lane change can finish: nothing to repair.
    too_fast = CutInPlan.model_validate({**base, "motorcycle_start_offset_m": 5, "ego_speed_kmh": 250})
    assert repair_maneuver(too_fast, sampled.model_copy(update={"context": sampled.context.model_copy(update={"ego_speed_kmh": 250})})) is None


def test_lane_change_must_end_a_second_before_the_five_second_scenario() -> None:
    sampled, selected = variant(), snapshot("Town01", 1)
    late = CutInPlan.model_validate({**sampled.context.model_dump(), "motorcycle_start_offset_m": 10,
                                     "trigger_time_s": 3.0, "lane_change_duration_s": 2.0, "desired_lead_gap_m": 5})
    issues = validate_cut_in(late, sampled, selected).issues
    assert [issue.code for issue in issues if issue.code == "CUT_IN_TOO_LATE"] == ["CUT_IN_TOO_LATE"]
    assert all(issue.recoverable for issue in issues)
    repaired = repair_maneuver(late, sampled)
    assert repaired is not None and repaired.trigger_time_s + repaired.lane_change_duration_s <= 4.0
    assert validate_cut_in(repaired, sampled, selected).valid
