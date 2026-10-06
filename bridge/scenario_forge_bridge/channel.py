"""Long-lived WebSocket channel to the backend: welcome, heartbeats with the CARLA probe, catalog sync,
simulator runs (run.assign), reconnect with backoff."""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from scenario_forge_bridge import api, runtime
from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_catalog import CarlaUnavailable
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.config import BridgeConfig, Connection
from scenario_forge_bridge.runner import RunExecutor, runner_problems

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


async def _sync_catalog(ws: ClientConnection, cfg: BridgeConfig, token: str, message: dict, log: Log) -> None:
    """Web asked for this machine's CARLA data: read every map and upload it to the requesting project."""
    request_id, connection_uid = message.get("request_id"), message["connection_uid"]
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    async def send(kind: str, **fields) -> None:
        await ws.send(json.dumps({"type": kind, "request_id": request_id, "connection_uid": connection_uid, **fields}))

    def worker() -> None:
        # Runs in a thread: CARLA calls block (loading a map takes seconds to minutes).
        try:
            for result in runtime.collect(cfg, maps=message.get("maps"),
                                  on_loading=lambda i, n, name: loop.call_soon_threadsafe(events.put_nowait, ("loading", i, n, name))):
                loop.call_soon_threadsafe(events.put_nowait, ("result", result))
        except CarlaUnavailable as exc:
            loop.call_soon_threadsafe(events.put_nowait, ("fatal", str(exc)))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(events.put_nowait, ("fatal", f"Lỗi khi đọc CARLA: {exc}"))
        finally:
            loop.call_soon_threadsafe(events.put_nowait, ("end",))

    log(f"Nhận yêu cầu đồng bộ dữ liệu CARLA ({connection_uid}).")
    thread = asyncio.create_task(asyncio.to_thread(worker))
    synced, failed = [], []
    while True:
        event = await events.get()
        if event[0] == "loading":
            _, index, total, name = event
            log(f"Đang đọc map {index}/{total}: {name}")
            await send("catalog.sync.progress", index=index, total=total, map_name=name, status="loading")
        elif event[0] == "result":
            result = event[1]
            if result.error:
                failed.append({"map_name": result.map_name, "error": result.error})
                log(f"  ✗ {result.map_name}: {result.error}")
                await send("catalog.sync.progress", index=result.index, total=result.total, map_name=result.map_name, status="failed", error=result.error)
                continue
            try:
                stored = await asyncio.to_thread(
                    api.upload_catalog, cfg.server, token, connection_uid, result.catalog, request_id,
                    on_retry=lambda n, wait, why, name=result.map_name: log(f"  ↻ Gửi lại {name} (lần {n}) sau {wait} giây: {why}"),
                )
            except api.BridgeApiError as exc:
                failed.append({"map_name": result.map_name, "error": str(exc)})
                log(f"  ✗ {result.map_name}: {exc}")
                await send("catalog.sync.progress", index=result.index, total=result.total, map_name=result.map_name, status="failed", error=str(exc))
                continue
            synced.append(result.map_name)
            log(f"  ✓ {result.map_name}: {len(result.catalog['spawn_points'])} spawn point, {len(result.catalog['waypoints'])} waypoint")
            await send("catalog.sync.progress", index=result.index, total=result.total, map_name=result.map_name, status="uploaded",
                       snapshot_id=stored.get("snapshot_id"))
        elif event[0] == "fatal":
            failed.append({"map_name": None, "error": event[1]})
            log(event[1])
        else:
            break
    await thread
    log(f"Đồng bộ xong: {len(synced)} map thành công, {len(failed)} lỗi.")
    await send("catalog.sync.done", synced=synced, failed=failed)


def _close_detail(exc: ConnectionClosed, opened_at: float | None) -> str:
    """Who closed and how: 1006 = dropped without a close frame (network / proxy), 1011 = keepalive ping timeout."""
    frame = exc.rcvd or exc.sent
    side = "máy chủ" if exc.rcvd else "Bridge" if exc.sent else "mạng"
    code = f"mã {frame.code}{' ' + frame.reason if frame.reason else ''}" if frame else "mã 1006, không có frame đóng"
    lasted = f", sau {time.monotonic() - opened_at:.0f} giây" if opened_at else ""
    return f"{side} đóng, {code}{lasted}"


def _apply_connections(cfg: BridgeConfig, items: list[dict]) -> None:
    cfg.connections = [Connection(**{key: item[key] for key in Connection.__dataclass_fields__}) for item in items]
    config_store.save(cfg)


async def _session(cfg: BridgeConfig, token: str, log: Log, stop_when_unlinked: bool, state: dict, executor: RunExecutor) -> bool:
    """One connected session. Returns True when the Bridge should stop (no project left)."""
    async with connect(
        cfg.ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        # run.assign carries the XOSC of every test case: far above the 1 MiB default.
        max_size=None,
    ) as ws:
        state["ws"] = ws
        state["opened_at"] = time.monotonic()
        heartbeat = asyncio.create_task(_heartbeats(ws, cfg, log))
        sync_task: asyncio.Task | None = None
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
                    await executor.resend_unacked()
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
                elif kind == "catalog.sync.request":
                    if sync_task is not None and not sync_task.done():
                        await ws.send(json.dumps({"type": "catalog.sync.done", "request_id": message.get("request_id"),
                                                  "connection_uid": message.get("connection_uid"), "synced": [],
                                                  "failed": [{"map_name": None, "error": "Bridge đang đồng bộ một yêu cầu khác"}]}))
                    else:
                        # Heartbeats and other messages keep flowing while the maps load.
                        sync_task = asyncio.create_task(_sync_catalog(ws, cfg, token, message, log))
                elif kind == "run.assign":
                    await executor.accept(message)
                elif kind == "ack":
                    executor.acknowledged(message)
                elif kind == "error":
                    if message.get("code") in ("RUN_NOT_FOUND", "TEST_CASE_NOT_IN_RUN", "INVALID_MESSAGE"):
                        executor.acknowledged(message)  # the backend will never take it: stop re-sending
                    ref = f" ({message['ref']})" if message.get("ref") else ""
                    log(f"Máy chủ báo lỗi{ref}: {message.get('code')} {message.get('message') or ''}".rstrip())
        finally:
            state["ws"] = None
            heartbeat.cancel()
            if sync_task is not None:
                sync_task.cancel()
    return False


async def run(cfg: BridgeConfig, token: str, log: Log, *, stop_when_unlinked: bool = True) -> None:
    state: dict = {"ws": None}

    async def send(message: dict) -> None:
        ws = state.get("ws")
        if ws is None:
            log(f"Mất kết nối, chưa gửi được {message.get('type')} (phiên {message.get('run_id')}).")
            return
        try:
            await ws.send(json.dumps(message))
        except ConnectionClosed:
            log(f"Mất kết nối khi gửi {message.get('type')} (phiên {message.get('run_id')}).")

    problems = runner_problems(cfg)
    log("Simulator Runner sẵn sàng." if not problems else "Simulator Runner chưa sẵn sàng: " + "; ".join(problems))
    # The executor outlives socket sessions so a reconnect never interrupts a running scenario.
    executor = RunExecutor(cfg, send, log)
    serving = asyncio.create_task(executor.serve())
    try:
        await _reconnect_loop(cfg, token, log, stop_when_unlinked, state, executor)
    finally:
        serving.cancel()


async def _reconnect_loop(cfg: BridgeConfig, token: str, log: Log, stop_when_unlinked: bool, state: dict, executor: RunExecutor) -> None:
    backoff = 1.0
    while True:
        state["connected"] = False
        state["opened_at"] = None
        try:
            if await _session(cfg, token, log, stop_when_unlinked, state, executor):
                return
            opened_at = state.get("opened_at")
            log("Máy chủ đóng kết nối" + (f" sau {time.monotonic() - opened_at:.0f} giây." if opened_at else "."))
        except InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                raise ChannelRejected("Máy chủ từ chối device token (Bridge đã bị gỡ hoặc token sai). Hãy chạy lại `pair <mã>`.") from exc
            log(f"Máy chủ trả về HTTP {exc.response.status_code}.")
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                raise ChannelRejected(f"Máy chủ từ chối kết nối: {exc.rcvd.reason or 'policy violation'}") from exc
            if exc.rcvd is not None and exc.rcvd.code == 4000:
                raise ChannelRejected("Một tiến trình Bridge khác vừa kết nối bằng cùng token; tiến trình này dừng.") from exc
            log(f"Mất kết nối tới máy chủ ({_close_detail(exc, state.get('opened_at'))}).")
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
