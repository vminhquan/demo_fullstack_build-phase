"""HTTP calls to the Scenario Forge backend (pairing and unpairing)."""
from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable

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


RETRY_DELAYS_S = (2, 5, 15)
# Render / proxies answer these while an instance restarts or deploys: worth another try.
RETRY_STATUS = {500, 502, 503, 504}


def upload_catalog(
    server: str,
    token: str,
    connection_uid: str,
    catalog: dict,
    request_id: str | None = None,
    *,
    on_retry: Callable[[int, float, str], None] | None = None,
) -> dict:
    """Sends one map's catalog.v1 (gzip) to the project of `connection_uid`. Returns {snapshot_id, map_name, created}.

    Network errors and 5xx are retried: the backend dedupes snapshots by content hash, so a repeat is harmless.
    """
    body = json.dumps({"connection_uid": connection_uid, "request_id": request_id, "catalog": catalog}, separators=(",", ":")).encode()
    compressed = gzip.compress(body, compresslevel=6)
    use_gzip = True
    attempt = 0
    while True:
        attempt += 1
        headers = {**_headers(token), "Content-Type": "application/json"}
        if use_gzip:
            headers["Content-Encoding"] = "gzip"
        problem: str
        try:
            # A big map can still be a few MB: allow time on slow uplinks.
            response = httpx.post(f"{server.rstrip('/')}/bridge/catalog", content=compressed if use_gzip else body, headers=headers, timeout=180)
        except httpx.TransportError as exc:
            problem = str(exc) or exc.__class__.__name__
        else:
            if response.status_code in (200, 201):
                return response.json()
            message = _error_message(response)
            # A backend older than the gzip support cannot parse the body: send it plain once.
            if use_gzip and response.status_code == 422 and "invalid catalog upload" in message.lower():
                use_gzip = False
                attempt -= 1
                continue
            if response.status_code not in RETRY_STATUS:
                raise BridgeApiError(message, response.status_code)
            problem = f"HTTP {response.status_code}: {message}"
        if attempt > len(RETRY_DELAYS_S):
            raise BridgeApiError(f"Không gửi được dữ liệu map lên máy chủ sau {attempt} lần: {problem}")
        delay = RETRY_DELAYS_S[attempt - 1]
        if on_retry:
            on_retry(attempt + 1, delay, problem)
        time.sleep(delay)
