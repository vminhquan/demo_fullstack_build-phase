from __future__ import annotations

import json
from typing import Any

from app.contracts import GenerationRequest
from app.llm.tools import SUBMIT_TOOL, ToolError
from fastapi.testclient import TestClient
from pydantic import SecretStr
from world import snapshot

# A motorcycle from the right lane merging in front of the ego and slowing down: passes the simulation checks.
VALID = {
    "map_name": "Town01", "title": "Xe máy tạt đầu rồi giảm tốc",
    "ego": {"blueprint_id": None, "start": {"road_id": 1, "lane_id": -1, "s": 20}, "speed_kmh": 50},
    "actors": [{"id": "moto1", "blueprint_id": "vehicle.yamaha.yzf", "lane_offset": -1, "ds_m": 12, "speed_kmh": 50}],
    "actions": [
        {"actor": "moto1", "at_time_s": 1, "type": "lane_change", "target_speed_kmh": None, "acceleration_mps2": None,
         "direction": "left", "duration_s": 2.0},
        {"actor": "moto1", "at_time_s": 3, "type": "speed", "target_speed_kmh": 30, "acceleration_mps2": 3,
         "direction": None, "duration_s": None},
    ],
    "critical_actor": "moto1", "expected": "near_miss", "weather_preset": "ClearNoon", "lighting": None, "road_surface": None,
    "duration_s": 8,
}
# The same motorcycle placed 250 m ahead: never close enough to be an incident.
FAR = {**VALID, "actors": [{**VALID["actors"][0], "ds_m": 250}]}
# A first turn reading two things at once; vehicles and weather come with the prefetched catalog.
READS = [("find_ego_starts", {"map_name": "Town01", "tags": None, "min_path_m": None, "min_left_lanes": None,
                              "min_right_lanes": 1, "limit": 5}),
         ("describe_position", {"map_name": "Town01", "road_id": 1, "lane_id": -1, "s": 20})]


class ScriptedLLM:
    """Replays one list of (tool name, arguments) per turn and records what it was sent."""

    model_name = "test-llm"

    def __init__(self, turns: list[list[tuple[str, dict]]]):
        self.turns, self.sent = list(turns), []

    def chat(self, messages, tools, tool_choice="required") -> dict[str, Any]:
        self.sent.append({"messages": list(messages), "tool_choice": tool_choice})
        turn = self.turns.pop(0) if self.turns else [(SUBMIT_TOOL, VALID)]
        number = len(self.sent)
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"t{number}-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for index, (name, args) in enumerate(turn)
        ]}


class FakeBackend:
    """The Backend's internal endpoints, answered from fixed data; records each call."""

    def __init__(self, *, save_error: bool = False, down: bool = False):
        self.calls: list[tuple[str, Any]] = []
        self.saved: list[dict] = []
        self.save_error, self.down = save_error, down

    def summary(self, snapshot_id):
        if self.down:
            raise ToolError("Backend không phản hồi: ConnectError")
        self.calls.append(("summary", snapshot_id))
        return {"map_name": "Town01", "total_sites": 1, "tags": {"straight": 1}, "speed_limits_kmh": {"30": 1},
                "max_length_m": 65.0}

    def search_sites(self, snapshot_id, filters):
        self.calls.append(("search_sites", filters))
        return {"total_matching": 1, "sites": [{"site_id": "Town01-site", "available_length_m": 65.0}]}

    def site_section(self, snapshot_id, site_id, section):
        self.calls.append((section, site_id))
        return {"site_id": site_id}

    def vehicles(self, snapshot_id, kind, seed=None):
        self.calls.append(("vehicles", (kind, seed)))
        items = [("vehicle.audi.a2", "car"), ("vehicle.tesla.model3", "car"), ("vehicle.yamaha.yzf", "motorcycle")]
        return {"kind": kind, "vehicles": [{"id": item, "kind": found, "length_m": None, "width_m": None}
                                           for item, found in items if kind in ("all", found)]}

    def weather_presets(self, snapshot_id):
        self.calls.append(("weather", snapshot_id))
        return {"weather_presets": ["ClearNoon", "HardRainNight"]}

    def save_scenario(self, payload):
        if self.save_error:
            raise ToolError("Backend trả lỗi 500.")
        self.saved.append(payload)
        return 99


def request(**changes) -> GenerationRequest:
    values = {"session_id": "session-1", "prompt": "Xe máy tạt đầu ô tô", "seed": 4,
              "selected_snapshots": [snapshot("Town01", 1, presets=["ClearNoon", "HardRainNight"])],
              "ego_blueprint_id": "vehicle.tesla.model3"}
    return GenerationRequest.model_validate({**values, **changes})


def test_http_boundary_requires_key_and_backend(monkeypatch) -> None:
    from app import api

    class Settings:
        agent_api_key = SecretStr("test-key")
        model_name = "test-llm"
        backend_url = None

    monkeypatch.setattr(api, "get_settings", Settings)
    monkeypatch.setattr(api, "from_settings", lambda settings: ScriptedLLM([READS]))
    monkeypatch.setattr(api, "_catalog_api", lambda url, key: FakeBackend())
    body = request().model_dump(mode="json")
    with TestClient(api.app) as client:
        assert client.post("/v1/scenarios/generate", json=body).status_code == 401
        missing = client.post("/v1/scenarios/generate", json=body, headers={"X-API-Key": "test-key"})
        Settings.backend_url = "http://backend"
        response = client.post("/v1/scenarios/generate", json=body, headers={"X-API-Key": "test-key"})
    assert missing.status_code == 503 and missing.json()["detail"]["code"] == "BACKEND_NOT_CONFIGURED"
    assert response.status_code == 200
    assert response.json()["scenarios"][0]["site_id"] == "1:-1:20"
