"""Long-lived WebSocket channel to the backend: welcome, heartbeats with the CARLA probe, reconnect with backoff."""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.config import BridgeConfig, Connection

HEARTBEAT_SECONDS = 15
MAX_BACKOFF_SECONDS = 30

Log = Callable[[str], None]


class ChannelRejected(Exception):
    """The backend refused the device token (revoked / unknown): reconnecting cannot help."""


async def _heartbeats(ws: ClientConnection, cfg: BridgeConfig, log: Log) -> None:
    last_reachable: bool | None = None
    while True:
        probe = await asyncio.to_thread(probe_carla, cfg.carla_host, cfg.carla_port)
        if probe.reachable != last_reachable:
            state = "đã chạy" if probe.reachable else f"chưa chạy ({probe.error})"
            log(f"CARLA {probe.host}:{probe.port} {state}")
            last_reachable = probe.reachable
        await ws.send(json.dumps({"type": "bridge.heartbeat", "carla": probe.payload()}))
        await asyncio.sleep(HEARTBEAT_SECONDS)


def _apply_connections(cfg: BridgeConfig, items: list[dict]) -> None:
    cfg.connections = [Connection(**{key: item[key] for key in Connection.__dataclass_fields__}) for item in items]
    config_store.save(cfg)


async def _session(cfg: BridgeConfig, token: str, log: Log, stop_when_unlinked: bool, state: dict) -> bool:
    """One connected session. Returns True when the Bridge should stop (no project left)."""
    async with connect(
        cfg.ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        heartbeat = asyncio.create_task(_heartbeats(ws, cfg, log))
        try:
            async for raw in ws:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "bridge.welcome":
                    state["connected"] = True
                    _apply_connections(cfg, message.get("connections", []))
                    names = ", ".join(f"{item.project_name} ({item.connection_uid})" for item in cfg.connections) or "chưa có project nào"
                    log(f"Đã kết nối máy chủ · Bridge {message.get('bridge_uid')} · {names}")
                    if stop_when_unlinked and not cfg.connections:
                        log("Bridge chưa được ghép với project nào. Dùng `pair <mã 6 số>` để ghép.")
                        return True
                elif kind == "connection.added":
                    item = message["connection"]
                    cfg.upsert_connection(Connection(**{key: item[key] for key in Connection.__dataclass_fields__}))
                    config_store.save(cfg)
                    log(f"Đã thêm project {item['project_name']} · mã kết nối {item['connection_uid']}")
                elif kind == "connection.revoked":
                    cfg.remove_connection(message["connection_uid"])
                    config_store.save(cfg)
                    log(f"Project đã gỡ kết nối {message['connection_uid']}")
                    if stop_when_unlinked and not cfg.connections:
                        log("Không còn project nào được ghép. Bridge dừng.")
                        return True
                elif kind == "error":
                    log(f"Máy chủ báo lỗi: {message.get('code')}")
        finally:
            heartbeat.cancel()
    return False


async def run(cfg: BridgeConfig, token: str, log: Log, *, stop_when_unlinked: bool = True) -> None:
    backoff = 1.0
    while True:
        state = {"connected": False}
        try:
            if await _session(cfg, token, log, stop_when_unlinked, state):
                return
            log("Máy chủ đóng kết nối.")
        except InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                raise ChannelRejected("Máy chủ từ chối device token (Bridge đã bị gỡ hoặc token sai). Hãy chạy lại `pair <mã>`.") from exc
            log(f"Máy chủ trả về HTTP {exc.response.status_code}.")
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                raise ChannelRejected(f"Máy chủ từ chối kết nối: {exc.rcvd.reason or 'policy violation'}") from exc
            if exc.rcvd is not None and exc.rcvd.code == 4000:
                raise ChannelRejected("Một tiến trình Bridge khác vừa kết nối bằng cùng token; tiến trình này dừng.") from exc
            log("Mất kết nối tới máy chủ.")
        except InvalidURI as exc:
            raise ChannelRejected(f"Địa chỉ máy chủ không hợp lệ: {cfg.ws_url}") from exc
        except (OSError, asyncio.TimeoutError) as exc:
            log(f"Không kết nối được {cfg.ws_url}: {exc or exc.__class__.__name__}")
        else:
            backoff = 1.0
            continue
        if state["connected"]:
            backoff = 1.0  # the last session worked: retry quickly
        wait = min(backoff, MAX_BACKOFF_SECONDS) + random.uniform(0, 0.5)
        log(f"Thử kết nối lại sau {wait:.0f} giây…")
        await asyncio.sleep(wait)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
