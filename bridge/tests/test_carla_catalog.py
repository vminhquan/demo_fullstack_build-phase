from __future__ import annotations

import json
import os
import subprocess
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
    carla.DESTROYED.clear()
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
    assert town03["format"] == "scenario-forge.catalog.v2" and town03["map_name"] == "Town03"
    assert "AnnotationColorLandscape" in town03["available_maps"]
    assert len(town03["waypoints"]) == 300 and town03["spawn_points"]
    assert town03["cut_in_sites"] == []  # Fake CARLA exposes no neighboring-lane topology.
    # number_of_wheels is an Int attribute in CARLA (as_str() would raise): read through as_int().
    wheels = {item["id"]: item["number_of_wheels"] for item in town03["vehicles"]}
    assert wheels == {"vehicle.tesla.model3": 4, "vehicle.yamaha.yzf": 2}
    assert town03["weather_presets"] == ["ClearNoon", "WetNight"]


def test_catalog_v2_facts(fake_carla) -> None:
    town03 = next(item for item in carla_catalog.collect("127.0.0.1", 2000) if item.map_name == "Town03").catalog
    assert town03["extraction_errors"] == []
    assert town03["opendrive_xml"].startswith("<OpenDRIVE") and len(town03["opendrive_hash"]) == 64
    waypoint = town03["waypoints"][0]
    assert waypoint["junction_id"] == 900 and waypoint["lane_change"] == "Left"
    assert waypoint["left_marking"] == {"type": "Broken", "color": "White", "lane_change": "Both"}
    assert waypoint["left_lane"] is None and waypoint["right_lane"] is None  # get_right_lane raised: no neighbor
    assert town03["waypoints"][1]["junction_id"] is None
    # 40 mph -> km/h; "no limit" is not a number and is skipped.
    assert town03["road_speeds"] == [{"road_id": 1, "from_s": 0.0, "max_kmh": 64.4}]
    junction = next(item for item in town03["junctions"] if item["junction_id"] == 900)
    assert junction["incoming_road_ids"] == [1] and junction["four_way_candidate"] is False
    assert junction["connections"][0]["lane_links"] == [{"from_lane": -1, "to_lane": -1}]
    assert junction["lane_paths"][0]["entry"]["road_id"] == 1 and junction["extent"]["x"] == 2.0
    [landmark] = town03["landmarks"]
    assert landmark["type"] == "1000001" and landmark["affected_lanes"] == [[-1, -1]] and landmark["yaw"] == 90.0
    [light] = town03["traffic_lights"]
    assert light["opendrive_id"] == "963" and light["group_actor_ids"] == [42] and light["red_s"] == 2.0
    assert len(light["stop_waypoints"]) == 1 and len(light["affected_lanes"]) == 1
    [crosswalk] = town03["crosswalks"]
    assert len(crosswalk["polygon"]) == 4 and crosswalk["center"] == {"x": 2.0, "y": 1.5, "z": 0.0}
    assert town03["topology"][0]["end"]["road_id"] == 2
    tesla = next(item for item in town03["vehicles"] if item["id"] == "vehicle.tesla.model3")
    assert (tesla["length_m"], tesla["width_m"], tesla["height_m"]) == (4.0, 2.0, 1.6)
    assert set(fake_carla.DESTROYED) == {"vehicle.tesla.model3", "vehicle.yamaha.yzf"}  # every probe actor removed
    assert town03["weather_parameters"]["ClearNoon"] == {"cloudiness": 10.0, "precipitation": 0.0, "sun_altitude_angle": 45.0}
    assert all(-180 <= item["yaw"] < 180 for item in town03["spawn_points"])


def test_unsupported_api_is_reported_not_fatal(fake_carla, monkeypatch) -> None:
    monkeypatch.delattr(fake_carla.Map, "get_crosswalks")
    town03 = next(item for item in carla_catalog.collect("127.0.0.1", 2000) if item.map_name == "Town03").catalog
    assert town03["crosswalks"] is None
    assert [item["part"] for item in town03["extraction_errors"]] == ["crosswalks"]
    assert town03["landmarks"]


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


def test_catalog_script_runs_in_runner_python_with_site_module() -> None:
    script = Path(carla_catalog.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--maps", "Town03"],
        env={**os.environ, "PYTHONPATH": FAKE},
        capture_output=True, text=True, check=True,
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    catalog = next(event["catalog"] for event in events if event["event"] == "result")
    assert catalog["map_name"] == "Town03"
    assert catalog["cut_in_sites"] == []


def test_approach_to_a_four_way_junction_is_tagged(fake_carla, monkeypatch) -> None:
    site = {"ego_lane": {"road_id": 1}, "ego_s": 0.0, "location_tags": ["straight", "junction_approach"],
            "junction_ahead": {"junction_id": 900, "distance_m": 80.0}}
    other = {**site, "junction_ahead": {"junction_id": 901, "distance_m": 80.0}, "location_tags": ["straight", "junction_approach"]}
    monkeypatch.setattr(carla_catalog, "extract_cut_in_sites", lambda waypoints: [dict(site), dict(other)])
    monkeypatch.setattr(carla_catalog.map_facts, "junctions", lambda *args: [
        {"junction_id": 900, "four_way_candidate": True}, {"junction_id": 901, "four_way_candidate": False}])
    town03 = next(item for item in carla_catalog.collect("127.0.0.1", 2000) if item.map_name == "Town03").catalog
    tags = [item["location_tags"] for item in town03["cut_in_sites"]]
    assert tags == [["straight", "junction_approach", "intersection_4way"], ["straight", "junction_approach"]]
