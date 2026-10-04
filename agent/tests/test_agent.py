"""Offline tests (no OpenAI calls): grounding geometry on the default Town10HD_Opt catalog, XOSC
schema validity and the HTTP contract."""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.contracts.catalog_v1 import CatalogV1
from app.scenario.grounding import CatalogGrounder, heading_diff, right_of
from app.scenario.schemas import ScenarioIR
from app.scenario.xosc import XoscExporter, validate_xosc

CATALOG_FILE = Path(__file__).resolve().parents[2] / "backend" / "app" / "modules" / "catalog" / "default_data" / "Town10HD_Opt.json"


@pytest.fixture(scope="module")
def catalog_json() -> dict:
    if not CATALOG_FILE.is_file():
        pytest.skip("default catalog not present")
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    data["content_hash"] = "test-hash"
    return data


@pytest.fixture(scope="module")
def grounder(catalog_json) -> CatalogGrounder:
    return CatalogGrounder(CatalogV1(**catalog_json))


def make_ir(road_type: str, actor: dict, speed: float = 40.0) -> ScenarioIR:
    return ScenarioIR(name="t", description="t", weather="rain", time_of_day_hour=14,
                      ego={"initial_speed_kmh": speed, "road_type": road_type}, actors=[actor])


def relative(grounded):
    ego, actor = grounded.ego, grounded.actors[0]
    fx, fy = math.cos(math.radians(ego.yaw)), math.sin(math.radians(ego.yaw))
    rx, ry = right_of(ego.yaw)
    dx, dy = actor.x - ego.x, actor.y - ego.y
    return dx * fx + dy * fy, dx * rx + dy * ry, heading_diff(actor.yaw, ego.yaw)


def test_profile_reports_hostable_layouts(grounder):
    profile = grounder.profile()
    assert profile.map_name == "Town10HD_Opt"
    assert "intersection_4way" in profile.road_types and "urban_straight" in profile.road_types
    assert profile.has_oncoming_lane


@pytest.mark.parametrize(
    ("road", "actor", "check"),
    [
        ("urban_straight", {"actor_type": "truck", "relative_position": "ahead_same_lane", "initial_distance_m": 26, "initial_speed_kmh": 40, "trigger": "sudden_brake", "trigger_distance_m": 16},
         lambda along, lateral, dyaw: 20 < along < 32 and abs(lateral) < 1.0 and dyaw < 15),
        ("intersection_4way", {"actor_type": "motorcycle", "relative_position": "ahead_adjacent_right", "initial_distance_m": 25, "initial_speed_kmh": 35, "trigger": "cut_in", "trigger_distance_m": 10},
         lambda along, lateral, dyaw: along > 15 and 2.0 < lateral < 5.0 and dyaw < 30),
        ("urban_straight", {"actor_type": "car", "relative_position": "oncoming", "initial_distance_m": 40, "initial_speed_kmh": 40, "trigger": "lane_departure", "trigger_distance_m": 25},
         lambda along, lateral, dyaw: along > 30 and lateral < -2.0 and dyaw > 150),
        ("intersection_4way", {"actor_type": "car", "relative_position": "crossing_from_left", "initial_distance_m": 32, "initial_speed_kmh": 45, "trigger": "red_light_violation", "trigger_distance_m": 20},
         lambda along, lateral, dyaw: lateral < -6.0 and 60 < dyaw < 120),
        ("urban_straight", {"actor_type": "pedestrian", "relative_position": "crossing_from_right", "initial_distance_m": 24, "initial_speed_kmh": 5, "trigger": "jaywalking", "trigger_distance_m": 15},
         lambda along, lateral, dyaw: lateral > 2.0 and 60 < dyaw < 120),
    ],
)
def test_actor_lands_where_the_ir_says(grounder, road, actor, check):
    grounded = grounder.ground(make_ir(road, actor))
    assert check(*relative(grounded)), relative(grounded)
    assert grounded.actors[0].method in {"lane", "roadside"}
    assert grounded.ir.map_name == "Town10HD_Opt"


def test_blueprints_come_from_catalog(grounder, catalog_json):
    grounded = grounder.ground(make_ir("urban_straight", {"actor_type": "pedestrian", "relative_position": "crossing_from_right", "initial_distance_m": 20, "initial_speed_kmh": 5}))
    vehicles = {item["id"] for item in catalog_json["vehicles"]}
    walkers = {item["id"] for item in catalog_json["walkers"]}
    assert grounded.ego.blueprint in vehicles
    assert grounded.actors[0].blueprint in walkers


def test_xosc_is_schema_valid_and_mirrors_y(grounder):
    grounded = grounder.ground(make_ir("urban_straight", {"actor_type": "car", "relative_position": "ahead_same_lane", "initial_distance_m": 25, "initial_speed_kmh": 30}))
    xml = XoscExporter(flip_y=True).to_xml_string(grounded)
    assert validate_xosc(xml)["schema_valid"]
    root = ET.fromstring(xml)
    assert root.find("./RoadNetwork/LogicFile").get("filepath") == "Town10HD_Opt"
    ego_pos = root.find("./Storyboard/Init/Actions/Private[@entityRef='hero']/PrivateAction/TeleportAction/Position/WorldPosition")
    assert float(ego_pos.get("y")) == pytest.approx(-grounded.ego.y, abs=1e-3)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_generate_requires_api_key(client, catalog_json):
    response = client.post("/v1/scenarios/generate", json={"prompt": "xe máy tạt đầu", "catalog": catalog_json})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_generate_offline_returns_grounded_xosc(client, catalog_json):
    response = client.post(
        "/v1/scenarios/generate",
        json={"prompt": "Xe máy tạt đầu ở ngã tư khi trời mưa", "catalog": catalog_json, "offline_mode": True},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generation_mode"] == "deterministic"
    assert body["catalog"] == {"map_name": "Town10HD_Opt", "carla_version": "0.9.16", "content_hash": "test-hash"}
    assert body["scenario_ir"]["map_name"] == "Town10HD_Opt"
    assert body["xosc_validation"]["schema_valid"] is True
    assert body["grounding"]["actors"][0]["placement"] == "lane"


def test_regulation_question_is_rejected(client, catalog_json):
    response = client.post(
        "/v1/scenarios/generate",
        json={"prompt": "What is the UN R152 regulation?", "catalog": catalog_json, "offline_mode": True},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NOT_A_SCENARIO_REQUEST"
