"""WebSocket hub: live Bridge sockets and the Start up / Simulator Runner pages listening per project.

Works with several backend processes (uvicorn --workers N or several instances on one database):
each process delivers to the sockets it holds and relays the message to the other processes over
Postgres LISTEN/NOTIFY on one channel. A Bridge socket lives in exactly one process, so a message
for a Bridge (or a "dispatch this run" request) is acted on only by the process that holds it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy import text

logger = logging.getLogger(__name__)

CHANNEL = "scenario_forge_hub"
# Postgres refuses NOTIFY payloads of 8000 bytes or more; bigger messages are delivered in this process only.
MAX_NOTIFY_BYTES = 7900

DispatchHandler = Callable[[int, int], Awaitable[None]]


class BridgeHub:
    def __init__(self) -> None:
        self._project_listeners: dict[int, set[WebSocket]] = defaultdict(set)
        self._bridges: dict[int, WebSocket] = {}
        # connection_uid -> latest catalog sync state, kept in step across processes by the relayed progress events.
        self.syncs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.origin = secrets.token_hex(8)
        self._listen_task: asyncio.Task | None = None
        # Set by the run dispatcher: (bridge_id, run_id) -> send run.assign over the local socket.
        self.on_dispatch: DispatchHandler | None = None

    # ---- Cross-process relay ----

    async def start(self, dsn: str) -> None:
        if self._listen_task is None:
            self._listen_task = asyncio.create_task(self._listen_forever(dsn.replace("postgresql+asyncpg://", "postgresql://")))

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None

    async def _listen_forever(self, dsn: str) -> None:
        delay = 1.0
        while True:
            connection: asyncpg.Connection | None = None
            try:
                connection = await asyncpg.connect(dsn)
                closed = asyncio.Event()
                connection.add_termination_listener(lambda _: closed.set())
                await connection.add_listener(CHANNEL, self._on_notify)
                delay = 1.0
                await closed.wait()
            except asyncio.CancelledError:
                if connection is not None:
                    await connection.close()
                raise
            except Exception:  # noqa: BLE001 - database restarting: retry with backoff
                logger.warning("Hub listener disconnected; retrying in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _on_notify(self, _connection: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            envelope = json.loads(payload)
        except ValueError:
            return
        if envelope.get("origin") == self.origin:
            return
        asyncio.get_running_loop().create_task(self._apply_remote(envelope))

    async def _apply_remote(self, envelope: dict[str, Any]) -> None:
        kind = envelope.get("kind")
        try:
            if kind == "project":
                await self._deliver_project(int(envelope["project_id"]), envelope["message"])
            elif kind == "bridge":
                await self._send_local(int(envelope["bridge_id"]), envelope["message"])
            elif kind == "dispatch" and self.on_dispatch is not None and self.is_local(int(envelope["bridge_id"])):
                await self.on_dispatch(int(envelope["bridge_id"]), int(envelope["run_id"]))
        except Exception:  # noqa: BLE001 - one bad relay must not stop the listener
            logger.exception("Hub relay %s failed", kind)

    async def _notify(self, envelope: dict[str, Any]) -> None:
        from app.shared.infrastructure.db import engine  # late import: the hub is imported by the db-free tests

        payload = json.dumps({**envelope, "origin": self.origin}, separators=(",", ":"), default=str)
        if len(payload.encode()) > MAX_NOTIFY_BYTES:
            logger.warning("Hub message %s too large to relay (%d bytes)", envelope.get("kind"), len(payload))
            return
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})
        except Exception:  # noqa: BLE001 - the local delivery already happened
            logger.warning("Hub relay of %s failed", envelope.get("kind"))

    # ---- Frontend listeners (Start up / Test Suite / Simulator Runner pages) ----

    async def add_listener(self, project_id: int, socket: WebSocket) -> None:
        async with self._lock:
            self._project_listeners[project_id].add(socket)

    async def remove_listener(self, project_id: int, socket: WebSocket) -> None:
        async with self._lock:
            self._project_listeners[project_id].discard(socket)
            if not self._project_listeners[project_id]:
                self._project_listeners.pop(project_id, None)

    async def publish_project(self, project_id: int, message: dict[str, Any]) -> None:
        await self._deliver_project(project_id, message)
        await self._notify({"kind": "project", "project_id": project_id, "message": message})

    async def _deliver_project(self, project_id: int, message: dict[str, Any]) -> None:
        if str(message.get("type", "")).startswith("catalog.sync") and message.get("connection_uid") and message.get("sync"):
            self.syncs[str(message["connection_uid"])] = message["sync"]
        async with self._lock:
            listeners = list(self._project_listeners.get(project_id, ()))
        for socket in listeners:
            try:
                await socket.send_json(message)
            except Exception:  # noqa: BLE001 - a closed tab must not break the others
                await self.remove_listener(project_id, socket)

    # ---- Bridge sockets ----

    async def attach_bridge(self, bridge_id: int, socket: WebSocket) -> WebSocket | None:
        """Registers the Bridge socket and returns the socket it replaces (same Bridge reconnecting)."""
        async with self._lock:
            previous = self._bridges.get(bridge_id)
            self._bridges[bridge_id] = socket
        return previous

    async def detach_bridge(self, bridge_id: int, socket: WebSocket) -> bool:
        """Removes the socket if it is still the current one; False when a newer socket already replaced it."""
        async with self._lock:
            if self._bridges.get(bridge_id) is not socket:
                return False
            del self._bridges[bridge_id]
            return True

    def is_local(self, bridge_id: int) -> bool:
        """This process holds the Bridge socket (use bridge_online() for "online in any process")."""
        return bridge_id in self._bridges

    async def send_bridge(self, bridge_id: int, message: dict[str, Any]) -> bool:
        """Sends over the local socket, else relays to the process that holds it. False = sent nowhere locally
        and could not be relayed; callers check presence with bridge_online() beforehand."""
        if self.is_local(bridge_id):
            return await self._send_local(bridge_id, message)
        await self._notify({"kind": "bridge", "bridge_id": bridge_id, "message": message})
        return True

    async def _send_local(self, bridge_id: int, message: dict[str, Any]) -> bool:
        socket = self._bridges.get(bridge_id)
        if socket is None:
            return False
        try:
            await socket.send_json(message)
            return True
        except Exception:  # noqa: BLE001
            logger.info("Bridge %s socket closed while sending", bridge_id)
            return False

    async def request_dispatch(self, bridge_id: int, run_id: int) -> None:
        """Have the process holding the Bridge socket send run.assign for this run."""
        if self.is_local(bridge_id) and self.on_dispatch is not None:
            await self.on_dispatch(bridge_id, run_id)
        else:
            await self._notify({"kind": "dispatch", "bridge_id": bridge_id, "run_id": run_id})

    def local_bridge_ids(self) -> list[int]:
        return list(self._bridges)

    async def close_bridge(self, bridge_id: int, code: int = 1000, reason: str = "") -> None:
        socket = self._bridges.get(bridge_id)
        if socket is not None:
            try:
                await socket.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001
                pass


hub = BridgeHub()
