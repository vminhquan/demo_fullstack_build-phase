"""Backend adapter to agent2's catalog-grounded cut-in generation API."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

import httpx

from app.shared.config import get_settings
from app.shared.domain.errors import AgentUnavailable, GenerationRejected


class AgentPort(Protocol):
    async def generate(
        self, *, prompt: str, catalog: dict[str, Any], auto_repair: bool, seed: int | None,
        constraints: dict[str, Any] | None = None, snapshot_id: int | None = None,
        content_hash: str | None = None, environment_code: str | None = None,
    ) -> dict[str, Any]: ...

    async def refine(self, *, spec: dict[str, Any], catalog: dict[str, Any], seed: int | None) -> dict[str, Any]: ...

    async def profile(self, *, catalog: dict[str, Any]) -> dict[str, Any]: ...

    async def title(self, *, description: str) -> dict[str, Any]: ...


class HttpAgentClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def generate(
        self, *, prompt: str, catalog: dict[str, Any], auto_repair: bool, seed: int | None,
        constraints: dict[str, Any] | None = None, snapshot_id: int | None = None,
        content_hash: str | None = None, environment_code: str | None = None,
    ) -> dict[str, Any]:
        selected = constraints or {}
        if selected.get("actor_type") not in (None, "motorcycle"):
            raise GenerationRejected("Agent2 chỉ hỗ trợ xe máy tạt đầu ô tô.", {}, "UNSUPPORTED_ACTOR")
        raw = json.dumps([prompt, catalog.get("map_name"), seed, selected], sort_keys=True, ensure_ascii=False)
        session_id = hashlib.sha256(raw.encode()).hexdigest()[:24]
        ref_hash = content_hash or catalog.get("content_hash") or hashlib.sha256(
            json.dumps(catalog, sort_keys=True).encode()
        ).hexdigest()
        weather_preset = environment_code if environment_code in catalog.get("weather_presets", []) else None
        weather = selected.get("weather")
        conditions = ["rain"] if weather in ("rain", "heavy_rain") else ["sunny"] if weather == "clear" else ["fog"] if weather == "fog" else []
        lighting = "night" if weather == "night" else "sunset" if weather == "dusk" else None
        if selected.get("time_of_day_hour") is not None:
            hour = selected["time_of_day_hour"]
            lighting = "night" if hour >= 19 or hour < 6 else "sunset" if hour >= 17 else "day"
        if weather_preset is not None:
            # The exact CARLA preset is authoritative; the legacy weather vocabulary is lossy.
            conditions, lighting = [], None
        payload = {
            "session_id": session_id, "prompt": prompt, "target_count": 1, "seed": seed,
            "max_proposal_attempts": 3 if auto_repair else 1,
            "selected_snapshots": [{
                "ref": {"snapshot_id": snapshot_id or 1, "map_name": catalog["map_name"], "content_hash": ref_hash},
                "catalog": catalog,
            }],
            "ego_blueprint_id": selected.get("ego_blueprint"),
            "weather_preset": weather_preset,
            "weather_conditions": conditions, "lighting": lighting,
        }
        response = await self._post("/v1/scenarios/generate", payload)
        if not response.get("scenarios"):
            raise GenerationRejected("Agent2 không sinh được kịch bản hợp lệ.", response, "NO_VALID_SCENARIO")
        scenario = response["scenarios"][0]
        plan = scenario["plan"]
        environment = plan["environment"]
        site = next((item for item in catalog.get("cut_in_sites") or [] if item["site_id"] == scenario["site_id"]), {})
        tags = site.get("location_tags", [])
        road_type = "urban_curve" if "curve" in tags else "urban_straight"
        weather_name = environment.get("weather_preset") or ""
        weather_label = "heavy_rain" if "HardRain" in weather_name else "rain" if "rain" in environment["weather_conditions"] else "fog" if "fog" in environment["weather_conditions"] else "clear"
        ir = {
            "name": f"motorcycle_cut_in_{scenario['scenario_id']}", "map_name": scenario["snapshot"]["map_name"],
            "ego": {"road_type": road_type, "blueprint": plan["ego_blueprint_id"]},
            "actors": [{"actor_type": "motorcycle", "trigger": "lane_change", "blueprint": plan["motorcycle_blueprint_id"]}],
            "weather": weather_label, "time_of_day_hour": environment["time_of_day_hour"],
            "weather_preset": environment.get("weather_preset"),
            "expected_outcome": {"expected_verdict": "UNKNOWN"},
        }
        return {
            "generation_mode": "agent2_cut_in", "model": response.get("model_name"),
            "scenario_ir": ir, "interpretation": {"scenario_type": "motorcycle_cut_in"},
            "validation": {"is_valid": True, "verdict": "VALID", "issues": scenario["validation"]["issues"]},
            "threat_score": {}, "grounding": {"map_name": scenario["snapshot"]["map_name"],
                                                "site_id": scenario["site_id"],
                                                "ego": {"blueprint": plan["ego_blueprint_id"]}},
            "xosc_validation": {"well_formed": True, "schema_checked": False},
            "retrieved_regulations": [], "warnings": [], "usage": {},
            "xosc": scenario["xosc"], "xosc_sha256": scenario["xosc_sha256"],
        }

    async def refine(self, *, spec: dict[str, Any], catalog: dict[str, Any], seed: int | None) -> dict[str, Any]:
        raise GenerationRejected("Biểu mẫu refine cũ chưa có hợp đồng tương ứng cho tình huống tạt đầu của agent2.", {}, "UNSUPPORTED_FORM")

    async def profile(self, *, catalog: dict[str, Any]) -> dict[str, Any]:
        tags = {tag for site in catalog.get("cut_in_sites") or [] for tag in site.get("location_tags", [])}
        return {"road_types": [name for tag, name in (("straight", "urban_straight"), ("curve", "urban_curve")) if tag in tags],
                "has_oncoming_lane": False}

    async def title(self, *, description: str) -> dict[str, Any]:
        return {"title": description.strip().splitlines()[0][:80] or "Xe máy tạt đầu ô tô"}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}{path}", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AgentUnavailable("The scenario Agent did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise AgentUnavailable("The scenario Agent is unreachable") from exc
        if response.status_code == 200:
            return response.json()
        error = _agent_error(response)
        if response.status_code == 422:
            raise GenerationRejected(error.get("message") or "The Agent could not generate this scenario", error.get("details") or {}, error.get("code"))
        # 401 means the Backend and Agent keys differ: a deployment problem, not the user's.
        raise AgentUnavailable(f"The scenario Agent failed ({response.status_code}: {error.get('code', 'UNKNOWN')})")


def _agent_error(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    detail = body.get("error", body.get("detail", {}))
    return detail if isinstance(detail, dict) else {"message": str(detail)}


def get_agent() -> AgentPort:
    settings = get_settings()
    if not settings.agent_service_url:
        raise AgentUnavailable("Scenario generation is not configured (AGENT_SERVICE_URL is empty)")
    return HttpAgentClient(settings.agent_service_url, settings.agent_api_key, settings.agent_timeout_seconds)
