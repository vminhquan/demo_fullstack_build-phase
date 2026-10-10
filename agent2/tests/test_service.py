from __future__ import annotations

from app.contracts import GenerationRequest
from app.cut_in.model import CutInPlan, PromptConstraints
from app.service import generate
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_sample import snapshot


class FakeLLM:
    model_name = "test-llm"

    def extract_constraints(self, prompt: str, *, tools=None, map_names=None, environments=None) -> PromptConstraints:
        return PromptConstraints(ego_speed_kmh=37, motorcycle_speed_kmh=43)

    def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None) -> CutInPlan:
        return CutInPlan.model_validate({**variant.context.model_dump(),
                                         "motorcycle_start_offset_m": 10,
                                         "trigger_time_s": 0.5,
                                         "lane_change_duration_s": 2,
                                         "desired_lead_gap_m": 5})


def test_generates_xosc_from_selected_snapshot() -> None:
    selected = snapshot("Town01", 1, presets=["ClearNoon"])
    request = GenerationRequest(session_id="session-1", prompt="Xe máy tạt đầu ô tô",
                                selected_snapshots=[selected], target_count=1, seed=4,
                                weather_preset="ClearNoon")
    result = generate(request, FakeLLM())
    assert result.status == "completed" and len(result.scenarios) == 1
    assert result.scenarios[0].snapshot == selected.ref
    assert "<OpenSCENARIO>" in result.scenarios[0].xosc
    assert generate(request, FakeLLM()).scenarios[0].xosc_sha256 == result.scenarios[0].xosc_sha256


def test_missing_site_index_returns_map_failure() -> None:
    selected = snapshot("Town01", 1)
    selected.catalog.cut_in_sites = None
    request = GenerationRequest(session_id="session-2", prompt="Xe máy tạt đầu ô tô",
                                selected_snapshots=[selected], target_count=1, seed=1)
    result = generate(request, FakeLLM())
    assert result.status == "failed" and result.map_failures[0].code == "SITE_INDEX_MISSING"


def test_http_boundary_requires_key_and_returns_agent2_contract(monkeypatch) -> None:
    from app import api

    class Settings:
        agent_api_key = SecretStr("test-key")
        model_name = "test-llm"
        backend_url = None

    monkeypatch.setattr(api, "get_settings", Settings)
    monkeypatch.setattr(api, "from_settings", lambda settings: FakeLLM())
    body = GenerationRequest(session_id="http-1", prompt="Xe máy tạt đầu ô tô",
                             selected_snapshots=[snapshot("Town01", 1, presets=["ClearNoon"])],
                             target_count=1, seed=4, weather_preset="ClearNoon").model_dump(mode="json")
    with TestClient(api.app) as client:
        assert client.post("/v1/scenarios/generate", json=body).status_code == 401
        response = client.post("/v1/scenarios/generate", json=body, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["scenarios"][0]["site_id"] == "Town01-site"
