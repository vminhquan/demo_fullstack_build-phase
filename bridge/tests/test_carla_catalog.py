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
    import carla

    carla.LOADS.clear()
    carla.DEAD["value"] = False
    carla.MAPS[:] = ["/Game/Carla/Maps/AnnotationColorLandscape", "/Game/Carla/Maps/Town01", "/Game/Carla/Maps/Town01_Opt",
                     "/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town10HD_Opt"]
    monkeypatch.setattr(carla_catalog, "ALIVE_WAIT_S", 0.01)
    yield carla
    sys.modules.pop("carla", None)


def test_skips_utility_maps_and_loads_each_road_network_once(fake_carla) -> None:
    results = list(carla_catalog.collect("127.0.0.1", 2000))
    # Open map first, then the others; AnnotationColorLandscape is not a road map.
    assert [item.map_name for item in results] == ["Town10HD_Opt", "Town01", "Town01_Opt", "Town03"]
    assert all(item.error is None for item in results)
    # Town01_Opt reuses Town01's roads: Town01 loaded once, Town01_Opt never; the open map is restored at the end.
    assert fake_carla.LOADS == ["Town01", "Town03", "Town10HD_Opt"]
    town01, town01_opt = results[1].catalog, results[2].catalog
    assert town01_opt["map_name"] == "Town01_Opt" and town01_opt["waypoints"] == town01["waypoints"]


def test_catalog_shape(fake_carla) -> None:
    town03 = next(item for item in carla_catalog.collect("127.0.0.1", 2000) if item.map_name == "Town03").catalog
    assert town03["format"] == "scenario-forge.catalog.v1" and town03["map_name"] == "Town03"
    assert "AnnotationColorLandscape" in town03["available_maps"]
    assert len(town03["waypoints"]) == 300 and town03["spawn_points"]
    # number_of_wheels is an Int attribute in CARLA (as_str() would raise): read through as_int().
    wheels = {item["id"]: item["number_of_wheels"] for item in town03["vehicles"]}
    assert wheels == {"vehicle.tesla.model3": 4, "vehicle.yamaha.yzf": 2}
    assert town03["weather_presets"] == ["ClearNoon", "WetNight"]


def test_load_opt_reads_every_build(fake_carla) -> None:
    list(carla_catalog.collect("127.0.0.1", 2000, load_opt=True))
    assert "Town01_Opt" in fake_carla.LOADS


def test_utility_map_named_explicitly_fails_cleanly(fake_carla) -> None:
    [result] = list(carla_catalog.collect("127.0.0.1", 2000, maps=["AnnotationColorLandscape"]))
    assert "OpenDRIVE" in result.error


def test_unresponsive_server_stops_the_run(fake_carla) -> None:
    fake_carla.MAPS[:] = ["/Game/Carla/Maps/Town01", "/Game/Carla/Maps/Town02", "/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town10HD_Opt"]
    seen = []
    with pytest.raises(carla_catalog.CarlaUnavailable, match="không phản hồi"):
        for result in carla_catalog.collect("127.0.0.1", 2000):
            seen.append((result.map_name, result.error is None))
    assert seen[:2] == [("Town10HD_Opt", True), ("Town01", True)]
    assert ("Town02", False) in seen and ("Town03", False) in seen
    # Town03 was skipped without trying to load it, and no restore was attempted on the dead server.
    assert fake_carla.LOADS == ["Town01", "Town02"]


def test_map_filter(fake_carla) -> None:
    assert [item.map_name for item in carla_catalog.collect("127.0.0.1", 2000, maps=["Carla/Maps/Town03"])] == ["Town03"]


def test_missing_carla_package_is_explained(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "carla", None)
    with pytest.raises(carla_catalog.CarlaUnavailable, match="setup-runner"):
        list(carla_catalog.collect("127.0.0.1", 2000))
