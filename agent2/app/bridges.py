"""Bridges dialled in over WebSocket, so the graph can run a valid XOSC on their CARLA with a no-brain ego.

The Bridge sits next to CARLA behind NAT and cannot be called, so it opens this socket itself; the Backend hands
it the URL and a signed token in bridge.welcome. XOSC and result then go agent2 ⇄ Bridge without the Backend.

  agent2 → Bridge  {"type": "validate.request", "request_id", "xosc", "xosc_sha256", "timeout_s"}
  Bridge → agent2  {"type": "validate.result", "request_id", "status": "ok" | "error", "collision",
                    "collision_count", "scenario_duration_s", "wall_time_ms", "error_code", "error_message"}

The graph runs in a worker thread (sync endpoint); sockets live on the event loop, so `validate` hands the
request to the loop and blocks the worker thread until the answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("agent2.bridges")

# Seconds agent2 waits beyond the Bridge's own run limit: map load, and a test run already holding CARLA.
WAIT_MARGIN_S = 60


def sign_bridge_token(secret: str, bridge_uid: str, expires: int) -> str:
    """`<bridge_uid>.<expires>.<hmac>`; the Backend signs it with the same BRIDGE_AGENT_SECRET."""
    body = f"{bridge_uid}.{expires}"
    return f"{body}.{hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()}"


def verify_bridge_token(secret: str, token: str | None, now: float | None = None) -> str | None:
    """The Bridge uid of a valid, unexpired token; None otherwise (and always without a secret)."""
    if not secret or not token or token.count(".") < 2:
        return None
    bridge_uid, expires, _ = token.rsplit(".", 2)
    if not bridge_uid or not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return None
    return bridge_uid if hmac.compare_digest(token, sign_bridge_token(secret, bridge_uid, int(expires))) else None


@dataclass(frozen=True)
class CarlaVerdict:
    """One CARLA run of the XOSC with the no-brain ego. `ran` False: no answer to judge (Bridge away, CARLA down)."""

    ran: bool
    collision: bool = False
    collision_count: int = 0
    scenario_duration_s: float | None = None
    wall_time_ms: int | None = None
    code: str | None = None
    message: str | None = None

    def as_metrics(self) -> dict[str, Any]:
        return {"ran": self.ran, "collision": self.collision, "collision_count": self.collision_count,
                "scenario_duration_s": self.scenario_duration_s, "wall_time_ms": self.wall_time_ms,
                "code": self.code, "message": self.message}


class Validator(Protocol):
    def validate(self, bridge_uid: str, xosc: str, xosc_sha256: str, timeout_s: float) -> CarlaVerdict: ...


@dataclass(frozen=True)
class CarlaCheck:
    """How build_xosc uses CARLA: optional skips when no verdict comes back, required fails the run."""

    validator: Validator
    mode: Literal["optional", "required"]
    timeout_s: float


class _Link:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.loop = asyncio.get_running_loop()
        self.send_lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def request(self, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        future: asyncio.Future[dict[str, Any]] = self.loop.create_future()
        self.pending[payload["request_id"]] = future
        try:
            async with self.send_lock:
                await self.websocket.send_json(payload)
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self.pending.pop(payload["request_id"], None)

    def resolve(self, message: dict[str, Any]) -> None:
        future = self.pending.get(str(message.get("request_id")))
        if future is not None and not future.done():
            future.set_result(message)

    def drop(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Bridge mất kết nối"))


class BridgeRegistry:
    """The Bridges connected to this process, by uid (one uvicorn worker: in-memory is the whole picture)."""

    def __init__(self) -> None:
        self._links: dict[str, _Link] = {}

    def connected(self, bridge_uid: str) -> bool:
        return bridge_uid in self._links

    async def serve(self, bridge_uid: str, websocket: WebSocket) -> None:
        """Accepted socket of one Bridge until it closes; a newer socket of the same Bridge replaces it."""
        link = _Link(websocket)
        previous = self._links.get(bridge_uid)
        self._links[bridge_uid] = link
        if previous is not None:
            previous.drop()
            await previous.websocket.close(code=4000, reason="replaced by a newer connection")
        logger.info("Bridge %s connected for CARLA validation", bridge_uid)
        try:
            await websocket.send_json({"type": "agent.welcome", "bridge_uid": bridge_uid})
            while True:
                message = await websocket.receive_json()
                if message.get("type") == "validate.result":
                    link.resolve(message)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - a malformed frame closes this Bridge only
            logger.exception("Bridge %s socket failed", bridge_uid)
        finally:
            link.drop()
            if self._links.get(bridge_uid) is link:
                del self._links[bridge_uid]
            logger.info("Bridge %s disconnected", bridge_uid)

    def validate(self, bridge_uid: str, xosc: str, xosc_sha256: str, timeout_s: float) -> CarlaVerdict:
        """Blocking, from a worker thread (never from the event loop): run the XOSC on that Bridge's CARLA."""
        link = self._links.get(bridge_uid)
        if link is None:
            return CarlaVerdict(ran=False, code="BRIDGE_NOT_CONNECTED", message=f"Bridge {bridge_uid} chưa kết nối agent2.")
        payload = {"type": "validate.request", "request_id": uuid.uuid4().hex, "xosc": xosc, "xosc_sha256": xosc_sha256,
                   "timeout_s": int(timeout_s)}
        wait_s = timeout_s + WAIT_MARGIN_S
        try:
            answer = asyncio.run_coroutine_threadsafe(link.request(payload, wait_s), link.loop).result(wait_s + 5)
        except TimeoutError:
            return CarlaVerdict(ran=False, code="VALIDATION_TIMEOUT", message=f"Bridge không trả kết quả sau {wait_s:g} s.")
        except ConnectionError as exc:
            return CarlaVerdict(ran=False, code="BRIDGE_DISCONNECTED", message=str(exc))
        if answer.get("status") != "ok":
            return CarlaVerdict(ran=False, code=str(answer.get("error_code") or "BRIDGE_ERROR"),
                                message=str(answer.get("error_message") or "")[-1000:] or None)
        return CarlaVerdict(
            ran=True, collision=bool(answer.get("collision")), collision_count=int(answer.get("collision_count") or 0),
            scenario_duration_s=answer.get("scenario_duration_s"), wall_time_ms=answer.get("wall_time_ms"),
        )
