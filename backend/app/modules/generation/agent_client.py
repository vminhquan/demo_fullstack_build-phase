"""Port + HTTP adapter to the scenario-generation Agent (agent/ service, POST /v1/scenarios/generate)."""
from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.shared.config import get_settings
from app.shared.domain.errors import AgentUnavailable, GenerationRejected


class AgentPort(Protocol):
    async def generate(
        self, *, prompt: str, catalog: dict[str, Any], auto_repair: bool, seed: int | None, constraints: dict[str, Any] | None = None
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
        self, *, prompt: str, catalog: dict[str, Any], auto_repair: bool, seed: int | None, constraints: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = {"prompt": prompt, "catalog": catalog, "auto_repair": auto_repair, "seed": seed, "constraints": constraints}
        return await self._post("/v1/scenarios/generate", payload)

    async def refine(self, *, spec: dict[str, Any], catalog: dict[str, Any], seed: int | None) -> dict[str, Any]:
        return await self._post("/v1/prompts/refine", {"spec": spec, "catalog": catalog, "seed": seed})

    async def profile(self, *, catalog: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1/catalog/profile", catalog)

    async def title(self, *, description: str) -> dict[str, Any]:
        return await self._post("/v1/titles", {"description": description})

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
    return body.get("error", {}) if isinstance(body, dict) else {}


def get_agent() -> AgentPort:
    settings = get_settings()
    if not settings.agent_service_url:
        raise AgentUnavailable("Scenario generation is not configured (AGENT_SERVICE_URL is empty)")
    return HttpAgentClient(settings.agent_service_url, settings.agent_api_key, settings.agent_timeout_seconds)
