"""Backend adapter to agent2's catalog-grounded scenario generation API (scenarios composed by its LLM)."""
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
        content_hash: str | None = None, environment_code: str | None = None, save_to: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def refine(self, *, spec: dict[str, Any], catalog: dict[str, Any], seed: int | None) -> dict[str, Any]: ...

    async def profile(self, *, catalog: dict[str, Any]) -> dict[str, Any]: ...

    async def title(self, *, description: str) -> dict[str, Any]: ...


def scenario_result(scenario: dict[str, Any], catalog: dict[str, Any], model_name: str | None) -> dict[str, Any]:
    """One agent2 scenario as a ScenarioGeneration result (with the XOSC under "xosc").

    The plan is whatever the LLM composed: an ego and any vehicles with timed actions. The critical actor comes first
    in scenario_ir.actors, so it is the test case's adversary."""
    plan = scenario["plan"]
    environment = plan["environment"]
    actors = sorted(plan.get("actors") or [], key=lambda actor: actor["id"] != plan.get("critical_actor"))
    first_action = {}
    for action in plan.get("actions") or []:
        first_action.setdefault(action["actor"], action["type"])
    metrics = plan.get("metrics") or {}
    weather_name = environment.get("weather_preset") or ""
    base_name = environment.get("base_preset") or weather_name
    conditions = environment["weather_conditions"]
    # Dust limits visibility like fog. A preset whose light was replaced (weather_preset null) is described by
    # its weather and hour instead of the preset name, e.g. dust at night -> "fog" at 22 h.
    weather_label = ("heavy_rain" if "HardRain" in base_name else "rain" if "rain" in conditions
                     else "fog" if {"fog", "dust"} & set(conditions) else "clear")
    ir = {
        "name": f"scenario_{scenario['scenario_id']}", "title": plan.get("title"), "map_name": scenario["snapshot"]["map_name"],
        "ego": {"road_type": "urban_road", "blueprint": plan["ego"]["blueprint_id"], "speed_kmh": plan["ego"]["speed_kmh"]},
        "actors": [{"id": actor["id"], "actor_type": actor["kind"], "blueprint": actor["blueprint_id"],
                    "speed_kmh": actor["speed_kmh"], "trigger": first_action.get(actor["id"]),
                    "critical": actor["id"] == plan.get("critical_actor")} for actor in actors],
        "weather": weather_label, "time_of_day_hour": environment["time_of_day_hour"],
        "weather_preset": environment.get("weather_preset"),
        "expected_outcome": {"expected_verdict": "COLLISION_EXPECTED" if plan.get("expected") == "collision" else "UNKNOWN"},
    }
    return {
        "generation_mode": "agent2_llm_scenario", "model": model_name,
        "scenario_ir": ir, "interpretation": {"scenario_type": "llm_composed", "title": plan.get("title")},
        "validation": {"is_valid": True, "verdict": "VALID", "issues": [], "incident": metrics},
        "threat_score": {}, "grounding": {"map_name": scenario["snapshot"]["map_name"], "site_id": scenario["site_id"],
                                            "ego": {"blueprint": plan["ego"]["blueprint_id"]}},
        "xosc_validation": {"well_formed": True, "schema_checked": False},
        "retrieved_regulations": [], "warnings": [], "usage": {},
        "xosc": scenario["xosc"], "xosc_sha256": scenario["xosc_sha256"],
    }


class HttpAgentClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def generate(
        self, *, prompt: str, catalog: dict[str, Any], auto_repair: bool, seed: int | None,
        constraints: dict[str, Any] | None = None, snapshot_id: int | None = None,
        content_hash: str | None = None, environment_code: str | None = None, save_to: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`save_to` (project, user, request hash) lets agent2's save node store the result through the internal
        endpoint; the answer then carries the stored `generation_id`."""
        selected = constraints or {}
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
            "max_proposal_attempts": 5 if auto_repair else 1,
            "selected_snapshots": [{
                "ref": {"snapshot_id": snapshot_id or 1, "map_name": catalog["map_name"], "content_hash": ref_hash},
                "catalog": catalog,
            }],
            "ego_blueprint_id": selected.get("ego_blueprint"),
            "weather_preset": weather_preset,
            "weather_conditions": conditions, "lighting": lighting,
            # The kind of road user the form locked for the actor causing the incident; agent2 checks it.
            "actor_type": selected.get("actor_type"),
            "save_to": save_to,
        }
        response = await self._post("/v1/scenarios/generate", payload)
        if not response.get("scenarios"):
            raise GenerationRejected("Agent2 không sinh được kịch bản hợp lệ.", response, "NO_VALID_SCENARIO")
        scenario = response["scenarios"][0]
        result = scenario_result(scenario, catalog, response.get("model_name"))
        if scenario.get("generation_id") is not None:
            result["generation_id"] = scenario["generation_id"]
        return result

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
        # 401 means the Backend and Agent keys differ: a deployment problem, not the user's. A 503 carries agent2's
        # reason (e.g. the OpenAI account has no credit left), shown as is.
        reason = f": {error['message']}" if error.get("message") else ""
        raise AgentUnavailable(f"The scenario Agent failed ({response.status_code}: {error.get('code', 'UNKNOWN')}){reason}")


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
