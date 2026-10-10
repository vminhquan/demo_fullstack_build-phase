from __future__ import annotations

import pytest
from app.catalog.find_sites import find_cut_in_sites
from app.catalog.models import SelectedSnapshot
from app.cut_in.model import PromptConstraints
from app.cut_in.sample import SamplingError, sample_variants


def snapshot(map_name: str, snapshot_id: int, *, presets: list[str] | None = None,
             vehicles: list[dict] | None = None) -> SelectedSnapshot:
    return SelectedSnapshot.model_validate({
        "ref": {"snapshot_id": snapshot_id, "map_name": map_name, "content_hash": f"{snapshot_id:064x}"},
        "catalog": {
            "carla_version": "0.9.16", "map_name": map_name,
            "vehicles": vehicles or [
                {"id": "vehicle.tesla.model3", "base_type": "car", "number_of_wheels": 4},
                {"id": "vehicle.yamaha.yzf", "number_of_wheels": 2},
                {"id": "vehicle.bh.crossbike", "base_type": "bicycle", "number_of_wheels": 2},
            ],
            "spawn_points": [{"x": 0, "y": 0}],
            "waypoints": [{"x": 0, "y": 0, "road_id": 1, "lane_id": -1}],
            "weather_presets": presets if presets is not None else ["Default", "ClearNoon", "WetNight", "HardRainNight"],
            "cut_in_sites": [{
                "site_id": f"{map_name}-site",
                "ego_lane": {"road_id": 1, "section_id": 0, "lane_id": -1},
                "motorcycle_lane": {"road_id": 1, "section_id": 0, "lane_id": -2},
                "ego_anchor": {"x": 0, "y": 0}, "motorcycle_anchor": {"x": 0, "y": 3.5},
                "available_length_m": 65, "location_tags": ["straight"],
                "verification_source": "carla_topology",
                "ego_s": 10, "motorcycle_s": 10,
                "s_direction": 1, "target_side": "left",
            }],
        },
    })


def test_seed_replays_samples_independent_of_input_order_and_covers_maps() -> None:
    first, second = snapshot("Town01", 1), snapshot("Town03", 2)
    sites = find_cut_in_sites([first, second]).sites
    a = sample_variants([first, second], sites, PromptConstraints(), target_count=4, seed=42)
    b = sample_variants([second, first], list(reversed(sites)), PromptConstraints(), target_count=4, seed=42)
    assert a == b
    assert a.seed == 42 and len(a.variants) == 4
    assert {variant.site.snapshot.map_name for variant in a.variants[:2]} == {"Town01", "Town03"}
    assert all(variant.context.motorcycle_blueprint_id == "vehicle.yamaha.yzf" for variant in a.variants)
    assert all(variant.context.environment.weather_preset != "Default" for variant in a.variants)


def test_user_weather_surface_and_speeds_are_kept() -> None:
    selected = snapshot("Town01", 1)
    constraints = PromptConstraints(weather_conditions=["rain"], lighting="night", road_surface="slippery",
                                    ego_speed_kmh=37, motorcycle_speed_kmh=43)
    result = sample_variants([selected], find_cut_in_sites([selected]).sites, constraints, target_count=1, seed=7)
    context = result.variants[0].context
    assert (context.ego_speed_kmh, context.motorcycle_speed_kmh) == (37, 43)
    assert context.environment.weather_preset == "HardRainNight"
    assert context.environment.road_surface == "slippery"
    assert context.environment.friction_scale_factor == 0.6
    assert context.sources["ego_speed_kmh"] == "user"
    assert context.environment.sources["road_surface"] == "user"
    assert result.map_failures == []


def test_no_weather_match_fails_only_that_map() -> None:
    rain = snapshot("Town01", 1, presets=["SoftRainNoon"])
    clear = snapshot("Town03", 2, presets=["ClearNoon"])
    result = sample_variants([rain, clear], find_cut_in_sites([rain, clear]).sites,
                             PromptConstraints(weather_conditions=["rain"]), target_count=2, seed=3)
    assert [variant.site.snapshot.map_name for variant in result.variants] == ["Town01", "Town01"]
    assert [(failure.snapshot.map_name, failure.code) for failure in result.map_failures] == [
        ("Town03", "WEATHER_NOT_AVAILABLE")]


def test_fog_requires_a_preset_in_the_selected_catalog() -> None:
    selected = snapshot("Town01", 1)
    result = sample_variants([selected], find_cut_in_sites([selected]).sites,
                             PromptConstraints(weather_conditions=["fog"]), target_count=1, seed=1)
    assert result.variants == []
    assert result.map_failures[0].code == "WEATHER_NOT_AVAILABLE"


def test_missing_motorcycle_is_reported_without_using_bicycle() -> None:
    selected = snapshot("Town01", 1, vehicles=[
        {"id": "vehicle.tesla.model3", "base_type": "car", "number_of_wheels": 4},
        {"id": "vehicle.bh.crossbike", "base_type": "bicycle", "number_of_wheels": 2},
    ])
    result = sample_variants([selected], find_cut_in_sites([selected]).sites, PromptConstraints(), target_count=1, seed=1)
    assert result.variants == []
    assert result.map_failures[0].code == "VEHICLE_NOT_AVAILABLE"


@pytest.mark.parametrize("constraints,code", [
    (PromptConstraints(weather_conditions=["snow"]), "UNSUPPORTED_WEATHER_CONDITION"),
    (PromptConstraints(road_surface="dry", weather_conditions=["rain"]), "ENVIRONMENT_CONFLICT"),
    (PromptConstraints(ego_speed_kmh=0), "UNSUPPORTED_SPEED"),
    (PromptConstraints(unsupported_requirements=["snow"], weather_conditions=[]), "UNSUPPORTED_REQUIREMENT"),
])
def test_unsupported_or_conflicting_constraints_fail_explicitly(constraints: PromptConstraints, code: str) -> None:
    selected = snapshot("Town01", 1)
    with pytest.raises(SamplingError) as error:
        sample_variants([selected], find_cut_in_sites([selected]).sites, constraints, target_count=1, seed=1)
    assert error.value.code == code


def test_ambiguities_are_notes_and_the_open_choices_are_sampled() -> None:
    selected = snapshot("Town01", 1)
    result = sample_variants([selected], find_cut_in_sites([selected]).sites,
                             PromptConstraints(ambiguities=["tốc độ không rõ đơn vị"]), target_count=1, seed=1)
    assert len(result.variants) == 1


def test_site_from_unselected_map_is_rejected() -> None:
    first, second = snapshot("Town01", 1), snapshot("Town03", 2)
    with pytest.raises(ValueError, match="not from a selected snapshot"):
        sample_variants([first], find_cut_in_sites([second]).sites, PromptConstraints(), target_count=1, seed=1)


def test_consecutive_seeds_walk_through_different_presets() -> None:
    names = ["ClearNoon", "ClearSunset", "ClearNight", "SoftRainNoon", "MidRainyNight", "HardRainSunset", "WetNoon", "CloudyNight"]
    selected = snapshot("Town01", 1, presets=names)
    sites = find_cut_in_sites([selected]).sites
    picked = [sample_variants([selected], sites, PromptConstraints(), target_count=1, seed=seed).variants[0]
              .context.environment.weather_preset for seed in range(1, 9)]
    assert sorted(picked) == sorted(names)  # 8 variants, 8 presets, none repeated
    rain = [sample_variants([selected], sites, PromptConstraints(weather_conditions=["rain"]), target_count=1, seed=seed)
            .variants[0].context.environment.weather_preset for seed in range(1, 4)]
    assert sorted(rain) == ["HardRainSunset", "MidRainyNight", "SoftRainNoon"]  # every rain intensity
