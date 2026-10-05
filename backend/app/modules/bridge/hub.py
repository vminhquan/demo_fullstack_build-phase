"""In-process WebSocket hub: live Bridge sockets and the Start up pages listening per project.

One backend process only (uvicorn without --workers). Running several instances needs a shared pub/sub
(RabbitMQ fan-out exchange or Redis) behind `publish_project` / `send_bridge`.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class BridgeHub:
    def __init__(self) -> None:
        self._project_listeners: dict[int, set[WebSocket]] = defaultdict(set)
        self._bridges: dict[int, WebSocket] = {}
        # connection_uid -> latest catalog sync state (in flight, or finished since this process started).
        self.syncs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # Frontend listeners (Start up / Test Suite pages)
    async def add_listener(self, project_id: int, socket: WebSocket) -> None:
        async with self._lock:
            self._project_listeners[project_id].add(socket)

    async def remove_listener(self, project_id: int, socket: WebSocket) -> None:
        async with self._lock:
            self._project_listeners[project_id].discard(socket)
            if not self._project_listeners[project_id]:
                self._project_listeners.pop(project_id, None)

    async def publish_project(self, project_id: int, message: dict[str, Any]) -> None:
        async with self._lock:
            listeners = list(self._project_listeners.get(project_id, ()))
        for socket in listeners:
            try:
                await socket.send_json(message)
            except Exception:  # noqa: BLE001 - a closed tab must not break the others
                await self.remove_listener(project_id, socket)

    # Bridge sockets
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

    def is_online(self, bridge_id: int) -> bool:
        return bridge_id in self._bridges

    async def send_bridge(self, bridge_id: int, message: dict[str, Any]) -> bool:
        socket = self._bridges.get(bridge_id)
        if socket is None:
            return False
        try:
            await socket.send_json(message)
            return True
        except Exception:  # noqa: BLE001
            logger.info("Bridge %s socket closed while sending", bridge_id)
            return False

    async def close_bridge(self, bridge_id: int, code: int = 1000, reason: str = "") -> None:
        socket = self._bridges.get(bridge_id)
        if socket is not None:
            try:
                await socket.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001
                pass


hub = BridgeHub()
