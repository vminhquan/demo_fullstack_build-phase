from __future__ import annotations

import asyncio
import json

from websockets.asyncio.server import serve

from scenario_forge_bridge import agent_link


class FakeExecutor:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def validate(self, message: dict) -> dict:
        self.asked.append(message)
        return {"status": "ok", "collision": True, "collision_count": 1, "scenario_duration_s": 3.0, "wall_time_ms": 10}


def test_the_bridge_answers_agent2_over_its_own_socket() -> None:
    async def main() -> tuple[dict, str | None, FakeExecutor]:
        answered: asyncio.Future = asyncio.get_running_loop().create_future()
        seen: dict = {}

        async def agent2(ws) -> None:
            seen["auth"] = ws.request.headers.get("Authorization")
            await ws.send(json.dumps({"type": "agent.welcome", "bridge_uid": "BRG-1"}))
            await ws.send(json.dumps({"type": "validate.request", "request_id": "r1", "xosc": "<x/>", "xosc_sha256": "h"}))
            answered.set_result(json.loads(await ws.recv()))

        executor, info = FakeExecutor(), {}
        async with serve(agent2, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            info.update(ws_url=f"ws://127.0.0.1:{port}", token="tok")
            link = asyncio.create_task(agent_link.run(lambda: info or None, executor, lambda _: None))
            result = await asyncio.wait_for(answered, 10)
            info.clear()  # the backend stops offering agent2: the link ends after this session
            link.cancel()
        return result, seen.get("auth"), executor

    result, auth, executor = asyncio.run(main())
    assert auth == "Bearer tok" and executor.asked[0]["request_id"] == "r1"
    assert result == {"type": "validate.result", "request_id": "r1", "status": "ok", "collision": True,
                      "collision_count": 1, "scenario_duration_s": 3.0, "wall_time_ms": 10}


def test_a_refused_token_stops_until_the_next_welcome() -> None:
    async def main() -> list[str]:
        async def agent2(ws) -> None:
            await ws.close(code=1008, reason="invalid bridge token")

        logs: list[str] = []
        async with serve(agent2, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            info = {"ws_url": f"ws://127.0.0.1:{port}", "token": "old"}
            await asyncio.wait_for(agent_link.run(lambda: info, FakeExecutor(), logs.append), 10)
        return logs

    assert any("từ chối token" in line for line in asyncio.run(main()))
