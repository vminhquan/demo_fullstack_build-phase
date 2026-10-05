from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scenario_forge_bridge import carla_catalog

FAKE = str(Path(__file__).parent / "fake_carla")


@pytest.fixture()
def fake_carla(monkeypatch):
    monkeypatch.syspath_prepend(FAKE)
    sys.modules.pop("carla", None)
    import carla  # noqa: F401  (the stand-in)

    yield sys.modules["carla"]
    sys.modules.pop("carla", None)


def test_collects_every_map_and_restores_the_open_one(fake_carla) -> None:
    results = list(carla_catalog.collect("127.0.0.1", 2000))
    assert [item.map_name for item in results] == ["Town01", "Town03", "Town10HD_Opt"]
    assert all(item.error is None for item in results)
    town03 = results[1].catalog
    assert town03["format"] == "scenario-forge.catalog.v1" and town03["map_name"] == "Town03"
    assert town03["available_maps"] == ["Town01", "Town03", "Town10HD_Opt"]
    assert len(town03["waypoints"]) == 300 and town03["spawn_points"]
    assert {item["id"] for item in town03["vehicles"]} == {"vehicle.tesla.model3", "vehicle.yamaha.yzf"}
    # number_of_wheels is an Int attribute in CARLA (as_str() would raise): read through as_int().
    wheels = {item["id"]: item["number_of_wheels"] for item in town03["vehicles"]}
    assert wheels == {"vehicle.tesla.model3": 4, "vehicle.yamaha.yzf": 2}
    assert town03["weather_presets"] == ["ClearNoon", "WetNight"]
    # Town10HD_Opt was open: loaded Town01, Town03, then back to Town10HD_Opt.
    assert fake_carla.LOADS[-3:] == ["Town01", "Town03", "Town10HD_Opt"]


def test_map_filter(fake_carla) -> None:
    assert [item.map_name for item in carla_catalog.collect("127.0.0.1", 2000, maps=["Carla/Maps/Town03"])] == ["Town03"]


def test_missing_carla_package_is_explained(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "carla", None)
    with pytest.raises(carla_catalog.CarlaUnavailable, match="pipx inject"):
        list(carla_catalog.collect("127.0.0.1", 2000))
