# ruff: noqa: F811  (fixtures shared with test_agent are used as test arguments)
"""Structured form: normalization, constraint enforcement and the refine -> generate HTTP flow (offline)."""
from __future__ import annotations

import pytest

from app.scenario.schemas import ScenarioIR
from app.scenario.spec import (
    ChosenValues,
    Range,
    ScenarioSpec,
    SpecUnsupported,
    apply_constraints,
    default_values,
    fit_values,
    normalize_spec,
)
from tests.test_agent import catalog_json, client, grounder  # noqa: F401  (pytest fixtures)

FORM = {
    "goal": "Tìm tình huống xe máy cut-in nguy hiểm",
    "scenario_type": "cut_in",
    "adversary_type": "motorcycle",
    "direction": "left",
    "road_type": "intersection",
    "weather": ["rain"],
    "lighting": "day",
    "objective": "critical",
    "ego_speed_kmh": {"min": 25, "max": 50},
    "actor_speed_kmh": {"min": 10, "max": 40},
    "initial_gap_m": {"min": 8, "max": 30},
    "trigger_distance_m": {"min": 5, "max": 20},
}


def test_normalize_fixes_ranges_and_reports_each_change(grounder):
    spec = ScenarioSpec(**{**FORM, "adversary_type": "pedestrian", "scenario_type": "crossing",
                           "actor_speed_kmh": {"min": 30, "max": 10}, "trigger_distance_m": {"min": 5, "max": 60}})
    result = normalize_spec(spec, grounder)
    fields = [item.field for item in result.adjustments]
    assert "actor_speed_kmh" in fields  # swapped, then capped at pedestrian sprint speed
    assert result.spec.actor_speed_kmh.max == 15.0
    assert result.spec.trigger_distance_m.max == 30.0  # never beyond the initial distance
    assert result.constraints.trigger.value == "jaywalking"
    assert result.constraints.relative_position.value == "crossing_from_left"
    assert result.road_type in ("intersection_4way", "intersection_3way")


def test_weather_rotates_with_seed_and_night_light(grounder):
    spec = ScenarioSpec(**{**FORM, "weather": ["clear", "rain"], "lighting": "night"})
    first, second = normalize_spec(spec, grounder, seed=0), normalize_spec(spec, grounder, seed=1)
    assert first.constraints.weather.value == "night" and second.constraints.weather.value == "rain"
    assert second.constraints.time_of_day_hour == 22


def test_unknown_ego_blueprint_is_dropped(grounder):
    result = normalize_spec(ScenarioSpec(**{**FORM, "ego_blueprint": "vehicle.not.real"}), grounder)
    assert result.spec.ego_blueprint is None
    assert any(item.field == "ego_blueprint" for item in result.adjustments)


def test_missing_adversary_in_catalog_is_rejected(grounder, monkeypatch):
    monkeypatch.setattr(grounder.catalog, "walkers", [])
    with pytest.raises(SpecUnsupported):
        normalize_spec(ScenarioSpec(**{**FORM, "adversary_type": "pedestrian"}), grounder)


def test_values_stay_in_range_and_trigger_is_fair():
    spec = ScenarioSpec(**FORM)
    values = fit_values(spec, ChosenValues(ego_speed_kmh=90, actor_speed_kmh=10, initial_gap_m=8, trigger_distance_m=2))
    assert values.ego_speed_kmh == 50 and values.initial_gap_m == 9.4
    assert values.trigger_distance_m <= values.initial_gap_m
    critical = default_values(spec)
    assert critical.ego_speed_kmh == 50 and critical.actor_speed_kmh == 10
    # closing 40 km/h -> 11.1 m/s * 0.8 s ~ 8.9 m + 0.5 m margin: the gap grows from 8 m so the trigger fits
    assert (critical.initial_gap_m, critical.trigger_distance_m) == (9.4, 9.4)


def test_apply_constraints_clamps_ir_and_drops_extra_actors(grounder):
    constraints = normalize_spec(ScenarioSpec(**FORM), grounder).constraints
    ir = ScenarioIR(name="t", description="t", weather="clear", time_of_day_hour=9,
                    ego={"initial_speed_kmh": 80, "road_type": "urban_straight"},
                    actors=[{"actor_type": "car", "relative_position": "oncoming", "initial_distance_m": 60, "initial_speed_kmh": 30},
                            {"actor_type": "motorcycle", "relative_position": "ahead_same_lane", "initial_distance_m": 50,
                             "initial_speed_kmh": 70, "trigger": "sudden_brake", "trigger_distance_m": 40}])
    fixed, warnings = apply_constraints(ir, constraints)
    assert fixed.ego.initial_speed_kmh == 50
    assert len(fixed.actors) == 1
    actor = fixed.actors[0]
    assert (actor.actor_type.value, actor.relative_position.value, actor.trigger.value) == ("motorcycle", "ahead_adjacent_left", "cut_in")
    assert (actor.initial_speed_kmh, actor.initial_distance_m, actor.trigger_distance_m) == (40, 30, 20)
    assert fixed.weather.value == "rain" and fixed.time_of_day_hour == 14
    assert warnings


def test_refine_then_generate_offline(client, catalog_json):  # noqa: F811
    headers = {"X-API-Key": "test-key"}
    refined = client.post("/v1/prompts/refine", json={"spec": FORM, "catalog": catalog_json, "offline_mode": True}, headers=headers)
    assert refined.status_code == 200, refined.text
    body = refined.json()
    assert body["refine_mode"] == "deterministic"
    assert "tạt đầu" in body["refined_prompt"]
    generated = client.post("/v1/scenarios/generate", headers=headers, json={
        "prompt": body["refined_prompt"], "catalog": catalog_json, "offline_mode": True, "constraints": body["constraints"],
    })
    assert generated.status_code == 200, generated.text
    ir = generated.json()["scenario_ir"]
    assert 25 <= ir["ego"]["initial_speed_kmh"] <= 50
    assert len(ir["actors"]) == 1
    actor = ir["actors"][0]
    assert actor["actor_type"] == "motorcycle" and actor["trigger"] == "cut_in"
    assert 8 <= actor["initial_distance_m"] <= 30 and 5 <= actor["trigger_distance_m"] <= 20
    assert ir["weather"] == "rain"
    assert generated.json()["xosc_validation"]["schema_valid"] is True


def test_range_helpers():
    assert Range(min=5, max=5).label("m") == "5 m"
    assert Range(min=1, max=3).clamp(9) == 3


def test_catalog_profile_endpoint(client, catalog_json):
    response = client.post("/v1/catalog/profile", json=catalog_json, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["map_name"] == "Town10HD_Opt"
    assert "intersection_4way" in body["road_types"]


def test_pedestrian_cut_in_becomes_crossing(grounder):
    result = normalize_spec(ScenarioSpec(**{**FORM, "adversary_type": "pedestrian"}), grounder)
    assert result.spec.scenario_type == "crossing"
    assert result.constraints.trigger.value == "jaywalking"


def test_clamped_trigger_is_made_fair_inside_ranges(grounder):
    spec = ScenarioSpec(**{**FORM, "adversary_type": "pedestrian", "scenario_type": "crossing",
                           "ego_speed_kmh": {"min": 33.3, "max": 37.5}, "actor_speed_kmh": {"min": 0, "max": 15},
                           "initial_gap_m": {"min": 8, "max": 14}, "trigger_distance_m": {"min": 5, "max": 9}})
    constraints = normalize_spec(spec, grounder).constraints
    ir = ScenarioIR(name="t", description="t", weather="rain", time_of_day_hour=22,
                    ego={"initial_speed_kmh": 37, "road_type": "intersection_4way"},
                    actors=[{"actor_type": "pedestrian", "relative_position": "crossing_from_left", "initial_distance_m": 8,
                             "initial_speed_kmh": 5, "trigger": "jaywalking", "trigger_distance_m": 7.5}])
    fixed, warnings = apply_constraints(ir, constraints)
    from app.scenario.validator import ScenarioGuardrailValidator
    assert ScenarioGuardrailValidator.validate_scenario(fixed).is_valid, ScenarioGuardrailValidator.validate_scenario(fixed).errors
    actor = fixed.actors[0]
    assert 5 <= actor.trigger_distance_m <= 9 and 8 <= actor.initial_distance_m <= 14 and 33.3 <= fixed.ego.initial_speed_kmh <= 37.5
    assert any("TTC" in text for text in warnings)
