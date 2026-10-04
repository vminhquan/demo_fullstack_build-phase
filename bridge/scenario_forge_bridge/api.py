"""HTTP calls to the Scenario Forge backend (pairing and unpairing)."""
from __future__ import annotations

import httpx

from scenario_forge_bridge import __version__
from scenario_forge_bridge.carla_probe import CarlaProbe


class BridgeApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail or f"HTTP {response.status_code}")


def _headers(token: str | None) -> dict[str, str]:
    headers = {"User-Agent": f"scenario-forge-bridge/{__version__}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def pair(server: str, code: str, *, token: str | None, name: str, hostname: str, os: str, carla: CarlaProbe) -> dict:
    """Redeems the 6-digit OTP. Returns {bridge_uid, device_token | None, connection{...}}."""
    payload = {"code": code, "name": name, "hostname": hostname, "os": os, "bridge_version": __version__, "carla": carla.payload()}
    try:
        response = httpx.post(f"{server.rstrip('/')}/bridge/pair", json=payload, headers=_headers(token), timeout=15)
    except httpx.HTTPError as exc:
        raise BridgeApiError(f"Không kết nối được tới máy chủ {server}: {exc}") from exc
    if response.status_code != 200:
        raise BridgeApiError(_error_message(response), response.status_code)
    return response.json()


def unpair(server: str, token: str, connection_uid: str) -> None:
    try:
        response = httpx.delete(f"{server.rstrip('/')}/bridge/connections/{connection_uid}", headers=_headers(token), timeout=15)
    except httpx.HTTPError as exc:
        raise BridgeApiError(f"Không kết nối được tới máy chủ {server}: {exc}") from exc
    if response.status_code not in (204, 404):
        raise BridgeApiError(_error_message(response), response.status_code)
