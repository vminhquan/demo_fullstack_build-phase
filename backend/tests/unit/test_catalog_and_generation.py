import gzip
import io
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.modules.catalog.contract import CatalogV1
from app.modules.catalog.importers import from_upload, from_worker_context
from app.modules.catalog.options import build_options, preset_label, vehicle_label
from app.modules.catalog.service import LATE_CATALOG_FIELDS, LATE_WAYPOINT_FIELDS, content_hash, pack_map_data, parse_catalog
from app.modules.generation.mapping import danger_level, environment_code, suggested_metadata
from app.shared.domain.errors import ValidationFailed
from app.shared.domain.policies import permissions_of
from app.shared.infrastructure.models import DangerLevel, ResponsibilityCode, RoleCode

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "app" / "modules" / "catalog" / "default_data" / "Town10HD_Opt.json"


def ir(**overrides) -> dict:
    base = {
        "name": "motorcycle_cut_in",
        "weather": "rain",
        "time_of_day_hour": 14,
        "map_name": "Town10HD_Opt",
        "ego": {"initial_speed_kmh": 40, "road_type": "intersection_4way"},
        "actors": [
            {"actor_type": "car", "relative_position": "behind_same_lane", "initial_distance_m": 10, "initial_speed_kmh": 30},
            {"actor_type": "bicycle", "relative_position": "crossing_from_right", "initial_distance_m": 20, "initial_speed_kmh": 12, "trigger": "jaywalking"},
        ],
    }
    base.update(overrides)
    return base


def test_default_catalog_is_valid_carla_0916() -> None:
    catalog = CatalogV1(**json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8")))
    assert catalog.carla_version == "0.9.16"
    assert catalog.map_name == "Town10HD_Opt"
    assert {v.base_type for v in catalog.vehicles} >= {"car", "truck", "motorcycle", "bicycle"}


def test_content_hash_ignores_sender_hash_and_detects_changes() -> None:
    raw = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    first = content_hash(CatalogV1(**raw))
    assert content_hash(CatalogV1(**{**raw, "content_hash": "forged"})) == first
    assert content_hash(CatalogV1(**{**raw, "map_name": "Town05"})) != first


def test_site_index_preserves_legacy_hash_but_changes_indexed_catalog_hash() -> None:
    raw = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    late = {"__all__": {"length_m", "width_m", "height_m"}}
    old_canonical = CatalogV1(**raw).model_dump(mode="json", exclude={
        "content_hash": True, "cut_in_sites": True, "opendrive_xml": True, **dict.fromkeys(LATE_CATALOG_FIELDS, True),
        "waypoints": {"__all__": set(LATE_WAYPOINT_FIELDS)}, "vehicles": late, "walkers": late,
    })
    expected = hashlib.sha256(json.dumps(old_canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert content_hash(CatalogV1(**raw)) == expected
    assert content_hash(CatalogV1(**{**raw, "cut_in_sites": []})) != expected


def v2_document() -> dict:
    raw = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    xodr = "<OpenDRIVE><road id='939'/></OpenDRIVE>"
    waypoint = {**raw["waypoints"][0], "junction_id": 895, "lane_change": "NONE",
                "left_marking": {"type": "SolidSolid", "color": "Yellow", "lane_change": "NONE"},
                "right_lane": {"road_id": 939, "section_id": 0, "lane_id": -2, "s": 0.0, "lane_type": "Sidewalk"}}
    return {
        **raw, "format": "scenario-forge.catalog.v2", "waypoints": [waypoint, *raw["waypoints"][1:]],
        "opendrive_xml": xodr, "opendrive_hash": hashlib.sha256(xodr.encode()).hexdigest(),
        "vehicles": [{**raw["vehicles"][0], "length_m": 3.7, "width_m": 1.8, "height_m": 1.5}, *raw["vehicles"][1:]],
        "cut_in_sites": [{
            "site_id": "939:0:-1:-2:0.0", "ego_lane": {"road_id": 939, "section_id": 0, "lane_id": -2},
            "motorcycle_lane": {"road_id": 939, "section_id": 0, "lane_id": -1},
            "ego_anchor": {"x": 0.0, "y": 3.5, "z": 0.0, "yaw": 0.0}, "motorcycle_anchor": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0},
            "available_length_m": 184.5,
            "location_tags": ["straight"], "verification_source": "carla_topology", "ego_s": 0.0, "motorcycle_s": 0.0,
            "s_direction": 1, "target_side": "left", "upstream_length_m": 40.0, "lane_change_allowed_throughout": True,
            "marking_between": ["Broken"], "max_heading_change_deg": 6.2, "first_junction_m": 150.0, "ends_at": "max_length",
            "lane_width_m": {"ego": 3.5, "motorcycle": 3.25}, "speed_limit_kmh": 64.4,
        }],
        "road_speeds": [{"road_id": 939, "from_s": 0.0, "max_kmh": 64.4}],
        "junctions": [{"junction_id": 895, "incoming_road_ids": [1, 2, 3], "incoming_road_count": 3,
                       "connections": [{"incoming_road_id": 1, "connecting_road_id": 939, "lane_links": [{"from_lane": -1, "to_lane": -1}]}]}],
        "landmarks": [{"id": "963", "type": "1000001", "road_id": 939, "s": 3.5, "affected_lanes": [[-1, -1]]}],
        "traffic_lights": [{"actor_id": 42, "opendrive_id": "963", "x": 1.0, "y": 2.0, "red_s": 2.0}],
        "crosswalks": [{"polygon": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}], "center": {"x": 2.7, "y": 1.0}}],
        "topology": [{"start": {"road_id": 1, "section_id": 0, "lane_id": -1, "s": 0.0},
                      "end": {"road_id": 939, "section_id": 0, "lane_id": -1, "s": 0.0}}],
        "weather_parameters": {"ClearNoon": {"cloudiness": 5.0}}, "extraction_errors": [{"part": "crosswalks", "error": "x"}],
    }


def test_catalog_v2_keeps_every_fact_and_checks_the_opendrive() -> None:
    raw = v2_document()
    catalog = parse_catalog(raw)
    document = catalog.model_dump(mode="json", exclude_none=True, exclude={"opendrive_xml"})
    for key in ("road_speeds", "junctions", "landmarks", "traffic_lights", "crosswalks", "topology", "weather_parameters", "extraction_errors"):
        assert document[key], key
    assert document["waypoints"][0]["left_marking"]["type"] == "SolidSolid"
    assert document["waypoints"][0]["right_lane"]["lane_type"] == "Sidewalk"
    assert document["vehicles"][0]["length_m"] == 3.7
    assert document["cut_in_sites"] == raw["cut_in_sites"]  # every corridor fact kept as sent
    assert "opendrive_xml" not in document
    # The OpenDRIVE text is covered by opendrive_hash, not hashed twice; any other fact changes the digest.
    assert content_hash(parse_catalog({**raw, "opendrive_xml": None})) == content_hash(catalog)
    assert content_hash(parse_catalog({**raw, "road_speeds": []})) != content_hash(catalog)
    with pytest.raises(ValidationFailed, match="opendrive_hash"):
        parse_catalog({**raw, "opendrive_xml": "<OpenDRIVE/>"})


def test_parse_catalog_rejects_non_catalog_documents() -> None:
    with pytest.raises(ValidationFailed):
        parse_catalog({"map_name": "Town05"})


def test_worker_context_converts_to_catalog() -> None:
    context = {
        "health": {"server_version": "0.9.16"},
        "static": {
            "map": "Carla/Maps/Town05",
            "available_maps": ["/Game/Carla/Maps/Town05"],
            "blueprints": ["vehicle.tesla.model3", "vehicle.yamaha.yzf", "walker.pedestrian.0001"],
            "spawn_points": [{"x": 1, "y": 2, "z": 0.6, "yaw_deg": 90}],
            "waypoints": [{"x": 1, "y": 2, "z": 0, "yaw_deg": 90, "road_id": 3, "lane_id": -1, "lane_width": 3.5, "junction": False, "lane_type": "LaneType.Driving"}],
        },
    }
    catalog = from_worker_context(context)
    assert catalog.map_name == "Town05" and catalog.available_maps == ["Town05"]
    assert {v.id: v.base_type for v in catalog.vehicles} == {"vehicle.tesla.model3": "car", "vehicle.yamaha.yzf": "motorcycle"}
    assert catalog.waypoints[0].lane_type == "Driving"


def test_primary_actor_is_the_triggered_one() -> None:
    suggestion = suggested_metadata({"scenario_ir": ir(), "threat_score": {"weighted_threat": 0.5}, "grounding": {"map_name": "Town10HD_Opt", "ego": {"blueprint": "vehicle.tesla.model3"}}})
    assert suggestion["adversary_type"] == "cyclist"
    assert suggestion["map_code"] == "Town10HD_Opt"
    assert suggestion["ego_vehicle_code"] == "vehicle.tesla.model3"
    assert suggestion["danger_level"] == "HIGH"
    assert "ai-generated" in suggestion["tag_names"] and "jaywalking" in suggestion["tag_names"]


def test_clear_night_maps_to_night_environment() -> None:
    assert environment_code(ir(weather="clear", time_of_day_hour=22)) == "night"
    assert environment_code(ir(weather="fog", time_of_day_hour=22)) == "fog"


@pytest.mark.parametrize(("threat", "expected"), [(0.1, DangerLevel.LOW), (0.4, DangerLevel.MEDIUM), (0.5, DangerLevel.HIGH), (0.8, DangerLevel.CRITICAL)])
def test_danger_level_bands(threat: float, expected: DangerLevel) -> None:
    assert danger_level({"weighted_threat": threat}, ir()) is expected


def test_expected_collision_is_at_least_high() -> None:
    scenario = ir(expected_outcome={"expected_verdict": "COLLISION_EXPECTED"})
    assert danger_level({"weighted_threat": 0.1}, scenario) is DangerLevel.HIGH


def test_admins_and_creators_import_catalogs() -> None:
    assert "catalog:import" in permissions_of(RoleCode.ADMIN, frozenset())
    assert "catalog:import" in permissions_of(RoleCode.MEMBER, frozenset({ResponsibilityCode.TESTCASE_CREATE}))
    assert "catalog:import" not in permissions_of(RoleCode.MEMBER, frozenset({ResponsibilityCode.TESTCASE_REVIEW}))


def _export_files() -> dict[str, bytes]:
    catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    wp = catalog["waypoints"][0]
    sp = catalog["spawn_points"][0]
    return {
        "01_server.json": json.dumps({"server_version": "0.9.16"}).encode(),
        "03_available_maps.json": json.dumps(["Town10HD_Opt"]).encode(),
        "04_blueprints.json": json.dumps([
            {"id": "vehicle.tesla.model3", "attributes": {"base_type": {"value": "ActorAttribute(id=base_type,type=str,value=car(const))"}}},
            {"id": "walker.pedestrian.0001", "attributes": {}},
        ]).encode(),
        "06_map_summary.json": json.dumps({"name": "Carla/Maps/Town10HD_Opt", "spawn_points": [{"location": {"x": sp["x"], "y": sp["y"], "z": sp["z"]}, "rotation": {"yaw": sp["yaw"]}}]}).encode(),
        "07_waypoints.json": json.dumps([{"road_id": wp["road_id"], "lane_id": wp["lane_id"], "lane_width": 3.5, "lane_type": "Driving", "is_junction": False,
                                         "transform": {"location": {"x": wp["x"], "y": wp["y"], "z": 0}, "rotation": {"yaw": wp["yaw"]}}}]).encode(),
    }


def test_upload_accepts_zipped_export_folder() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in _export_files().items():
            archive.writestr(f"20260924T122139Z/02_server_actor_state/{name}", data)
        archive.writestr("20260924T122139Z/export_manifest.json", b"{}")
    catalog = from_upload("export.zip", buffer.getvalue())
    assert (catalog.map_name, catalog.carla_version, len(catalog.vehicles), len(catalog.walkers)) == ("Town10HD_Opt", "0.9.16", 1, 1)
    assert catalog.vehicles[0].base_type == "car"


def test_upload_accepts_catalog_json_and_explains_wrong_files() -> None:
    assert from_upload("catalog.json", DEFAULT_CATALOG.read_bytes()).map_name == "Town10HD_Opt"
    with pytest.raises(ValidationFailed, match="zip the whole export folder"):
        from_upload("export_manifest.json", json.dumps({"static_summary": {}}).encode())
    with pytest.raises(ValidationFailed, match="missing"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("x/02_server_actor_state/01_server.json", b"{}")
        from_upload("export.zip", buffer.getvalue())


def _row(snapshot_id: int, **overrides) -> dict:
    catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    row = {"id": snapshot_id, "source": "DEFAULT", "is_default": True, "map_name": catalog["map_name"], "carla_version": catalog["carla_version"],
           "vehicles": catalog["vehicles"], "walkers": catalog["walkers"], "available_maps": catalog["available_maps"], "weather_presets": catalog["weather_presets"]}
    row.update(overrides)
    return row


def test_metadata_options_come_from_catalog_data() -> None:
    options = build_options([_row(1)])
    maps = {item["code"]: item for item in options["maps"]}
    assert maps["Town10HD_Opt"]["has_lane_data"] and not maps["Town05"]["has_lane_data"]
    assert options["maps"][0]["code"] == "Town10HD_Opt"  # maps with lane data first
    assert "vehicle.tesla.model3" in {v["code"] for v in options["ego_vehicles"]}
    assert "vehicle.yamaha.yzf" not in {v["code"] for v in options["ego_vehicles"]}  # motorcycles are not egos
    assert [a["code"] for a in options["adversary_types"]][:3] == ["pedestrian", "motorcycle", "cyclist"]
    environments = {e["code"]: e for e in options["environments"]}
    assert environments["heavy_rain"]["group"] == "standard"
    assert environments["HardRainNight"]["group"] == "carla_preset" and "Default" not in environments


def test_metadata_options_merge_sources_and_track_snapshots() -> None:
    worker = _row(2, source="WORKER", is_default=False, map_name="Town05", carla_version="0.9.15", vehicles=[{"id": "vehicle.tesla.model3", "base_type": "car"}], walkers=[], weather_presets=[])
    options = build_options([_row(1), worker])
    town05 = next(m for m in options["maps"] if m["code"] == "Town05")
    assert town05["has_lane_data"] and town05["snapshot_ids"] == [2] and set(town05["sources"]) == {"DEFAULT", "WORKER"}
    town10 = next(m for m in options["maps"] if m["code"] == "Town10HD_Opt")
    assert town10["carla_versions"] == ["0.9.16"]  # not the 0.9.15 server that merely lists it
    tesla = next(v for v in options["ego_vehicles"] if v["code"] == "vehicle.tesla.model3")
    assert tesla["snapshot_ids"] == [1, 2] and tesla["label"] == "Tesla Model3"
    assert build_options([]) == {"maps": [], "ego_vehicles": [], "adversary_types": [], "environments": []}


def test_labels_are_readable() -> None:
    assert preset_label("HardRainNight") == "Mưa to, ban đêm"
    assert preset_label("UnknownPreset") == "UnknownPreset"
    assert vehicle_label("vehicle.mercedes.coupe_2020") == "Mercedes Coupe 2020"


def test_agent_client_maps_cut_in_request_and_response(monkeypatch) -> None:
    import asyncio

    import httpx

    from app.modules.generation.agent_client import HttpAgentClient

    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"model_name": "gpt-4o-mini", "scenarios": [{
            "scenario_id": "abc", "snapshot": {"map_name": "Town01"}, "site_id": "site-1",
            "plan": {"ego_blueprint_id": "vehicle.tesla.model3", "motorcycle_blueprint_id": "vehicle.yamaha.yzf",
                     "environment": {"weather_preset": "ClearNoon", "weather_conditions": ["sunny"], "time_of_day_hour": 12}},
            "validation": {"issues": []}, "xosc": "<OpenSCENARIO/>", "xosc_sha256": "a" * 64,
        }]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    client = HttpAgentClient("http://agent2", "key", 5)
    catalog = {"map_name": "Town01", "carla_version": "0.9.16", "weather_presets": ["ClearNoon"],
               "cut_in_sites": [{"site_id": "site-1", "location_tags": ["straight"]}]}
    result = asyncio.run(client.generate(prompt="Xe máy tạt đầu ô tô", catalog=catalog, auto_repair=True, seed=1,
                                         constraints={"ego_blueprint": "vehicle.tesla.model3"}, snapshot_id=42,
                                         content_hash="b" * 64, environment_code="ClearNoon"))
    assert calls[0][0] == "/v1/scenarios/generate"
    request = calls[0][1]
    assert request["selected_snapshots"][0]["ref"] == {"snapshot_id": 42, "map_name": "Town01", "content_hash": "b" * 64}
    assert request["ego_blueprint_id"] == "vehicle.tesla.model3" and request["weather_preset"] == "ClearNoon"
    assert result["generation_mode"] == "agent2_cut_in" and result["scenario_ir"]["actors"][0]["actor_type"] == "motorcycle"


@pytest.mark.parametrize(
    ("code", "expected"),
    [("heavy_rain", ("heavy_rain", 14)), ("night", ("night", 22)), ("HardRainNight", ("heavy_rain", 22)),
     ("ClearSunset", ("dusk", 18)), ("WetCloudyNoon", ("clear", 14)), ("MidRainyNight", ("rain", 22))],
)
def test_environment_metadata_becomes_weather_and_hour(code: str, expected: tuple[str, int]) -> None:
    from app.modules.generation.mapping import environment_constraints

    assert environment_constraints(code) == expected


def test_metadata_constraints_pin_ego_and_adversary() -> None:
    from app.modules.generation.mapping import metadata_constraints

    result = metadata_constraints(ego_vehicle_code="vehicle.audi.a2", adversary_type="cyclist", environment_code="rain")
    assert result == {"weather": "rain", "time_of_day_hour": 14, "ego_blueprint": "vehicle.audi.a2", "actor_type": "bicycle"}


def test_metadata_constraints_leave_unpicked_values_to_the_agent() -> None:
    from app.modules.generation.mapping import metadata_constraints

    assert metadata_constraints(ego_vehicle_code=None, adversary_type="cyclist", environment_code=None) == {"actor_type": "bicycle"}
    assert metadata_constraints(ego_vehicle_code=None, adversary_type=None, environment_code=None) == {}


@pytest.mark.parametrize(("weather", "hour", "expected"), [("rain", 22, "MidRainyNight"), ("heavy_rain", 18, "HardRainSunset"), ("rain", 14, "rain")])
def test_environment_keeps_rain_after_dark(weather: str, hour: int, expected: str) -> None:
    from app.modules.generation.mapping import environment_constraints

    assert environment_code(ir(weather=weather, time_of_day_hour=hour)) == expected
    back_weather, back_hour = environment_constraints(expected)
    assert back_weather == weather and (back_hour >= 17) == (hour >= 17)


def test_clear_dusk_is_dusk() -> None:
    assert environment_code(ir(weather="clear", time_of_day_hour=18)) == "dusk"


def test_every_generation_needs_a_car_ego_from_its_snapshot() -> None:
    from types import SimpleNamespace

    from app.modules.generation.router import require_car_ego

    snapshot = SimpleNamespace(map_name="Town10HD_Opt", catalog={"vehicles": [
        {"id": "vehicle.tesla.model3", "base_type": "car"},
        {"id": "vehicle.carlamotors.carlacola", "base_type": "truck"},
    ]})
    assert require_car_ego(snapshot, "vehicle.tesla.model3") == "vehicle.tesla.model3"
    for code in (None, "vehicle.carlamotors.carlacola", "vehicle.unknown"):
        with pytest.raises(ValidationFailed):
            require_car_ego(snapshot, code)


def test_opt_map_shares_the_stored_map_data_and_it_round_trips():
    plain = parse_catalog(v2_document())  # Town10HD_Opt
    opt = parse_catalog({**v2_document(), "map_name": "Town10HD"})
    stored = [
        pack_map_data(catalog.model_dump(mode="json", exclude_none=True)["waypoints"], catalog.opendrive_hash, catalog.opendrive_xml)
        for catalog in (plain, opt)
    ]
    assert content_hash(plain) != content_hash(opt)
    assert stored[0]["data_hash"] == stored[1]["data_hash"]
    assert json.loads(gzip.decompress(stored[0]["waypoints_gz"])) == plain.model_dump(mode="json", exclude_none=True)["waypoints"]
    assert gzip.decompress(stored[0]["opendrive_gz"]).decode() == plain.opendrive_xml


def test_map_data_hash_changes_with_the_opendrive():
    waypoints = [{"road_id": 1, "lane_id": -1, "s": 0.0}]
    assert pack_map_data(waypoints, "a" * 64, None)["data_hash"] != pack_map_data(waypoints, "b" * 64, None)["data_hash"]
    assert pack_map_data(waypoints, None, None)["opendrive_gz"] is None


def test_reloading_the_map_in_carla_does_not_make_a_new_snapshot():
    first = v2_document()
    again = json.loads(json.dumps(first))
    again["traffic_lights"][0]["actor_id"] = 4243
    again["traffic_lights"][0]["group_actor_ids"] = [4243, 4252]
    assert content_hash(parse_catalog(first)) == content_hash(parse_catalog(again))
    again["traffic_lights"][0]["opendrive_id"] = "964"
    assert content_hash(parse_catalog(first)) != content_hash(parse_catalog(again))
