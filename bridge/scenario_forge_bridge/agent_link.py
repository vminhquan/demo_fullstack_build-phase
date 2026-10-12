"""Second WebSocket, straight to agent2: it sends a just-generated XOSC (validate.request) and gets back whether the
no-brain ego collided on this machine's CARLA, without a round trip through the backend.

The backend hands out the URL and a signed token in bridge.welcome (field `agent`); a fresh token comes with every
welcome, and each reconnect uses the latest one. Runs share RunExecutor.carla with test runs, so CARLA plays one
scenario at a time.
"""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from scenario_forge_bridge.runner import RunExecutor

MAX_BACKOFF_SECONDS = 30

Log = Callable[[str], None]
# The latest {"ws_url", "token"} from bridge.welcome; None once the backend stops offering it.
Current = Callable[[], "dict | None"]


async def _answer(ws: ClientConnection, message: dict, executor: RunExecutor, log: Log) -> None:
    result = await executor.validate(message)
    try:
        await ws.send(json.dumps({"type": "validate.result", "request_id": message.get("request_id"), **result}))
    except ConnectionClosed:
        log("Mất kết nối agent2 trước khi gửi kết quả kiểm tra; agent2 sẽ coi là chưa kiểm tra được.")


async def _session(info: dict, executor: RunExecutor, log: Log) -> None:
    tasks: set[asyncio.Task] = set()
    async with connect(
        info["ws_url"],
        additional_headers={"Authorization": f"Bearer {info['token']}"},
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        # validate.request carries a whole XOSC.
        max_size=None,
    ) as ws:
        try:
            async for raw in ws:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "agent.welcome":
                    log(f"Đã kết nối agent2 để kiểm tra kịch bản trên CARLA ({info['ws_url']}).")
                elif kind == "validate.request":
                    # Answer in the background: the socket keeps reading (pings, more requests) while CARLA runs.
                    task = asyncio.create_task(_answer(ws, message, executor, log))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        finally:
            for task in tasks:
                task.cancel()


async def run(current: Current, executor: RunExecutor, log: Log) -> None:
    """Keeps the agent2 socket open while the backend offers one; returns when it stops or agent2 refuses the token
    (the next bridge.welcome starts it again with a new token)."""
    backoff = 1.0
    while (info := current()) is not None:
        try:
            await _session(info, executor, log)
            log("agent2 đóng kết nối kiểm tra kịch bản.")
            backoff = 1.0
        except InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                # agent2 closes before accepting a bad or expired token: the handshake answers 403.
                log("agent2 từ chối token kiểm tra kịch bản; đợi token mới từ máy chủ.")
                return
            log(f"agent2 trả về HTTP {exc.response.status_code} khi kết nối kiểm tra kịch bản.")
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                log("agent2 từ chối token kiểm tra kịch bản; đợi token mới từ máy chủ.")
                return
            if exc.rcvd is not None and exc.rcvd.code == 4000:
                log("Một tiến trình Bridge khác đã kết nối agent2 bằng cùng Bridge; tiến trình này ngừng kiểm tra.")
                return
            log("Mất kết nối agent2 (kiểm tra kịch bản).")
        except InvalidURI:
            log(f"Địa chỉ agent2 không hợp lệ: {info['ws_url']}")
            return
        except (OSError, asyncio.TimeoutError) as exc:
            log(f"Không kết nối được agent2 {info['ws_url']}: {exc or exc.__class__.__name__}")
        await asyncio.sleep(min(backoff, MAX_BACKOFF_SECONDS) + random.uniform(0, 0.5))
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
