from __future__ import annotations

import threading

import pytest

from app.bridges import BridgeRegistry, CarlaCheck, CarlaVerdict, sign_bridge_token, verify_bridge_token
from app.llm.tools import SUBMIT_TOOL
from app.service import generate
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_graph import tool_answers
from test_service import READS, VALID, FakeBackend, ScriptedLLM, request


class FakeBridges:
    """Answers each validate call with the next verdict and records what it was asked."""

    def __init__(self, *verdicts: CarlaVerdict):
        self.verdicts, self.calls = list(verdicts), []

    def validate(self, bridge_uid, xosc, xosc_sha256, timeout_s):
        self.calls.append((bridge_uid, xosc_sha256, timeout_s))
        return self.verdicts.pop(0)


CRASH = CarlaVerdict(ran=True, collision=True, collision_count=1, scenario_duration_s=6.2)
MISS = CarlaVerdict(ran=True, collision=False, scenario_duration_s=12.0)
AWAY = CarlaVerdict(ran=False, code="BRIDGE_NOT_CONNECTED", message="Bridge BRG-1 chưa kết nối agent2.")


def test_tokens_are_signed_for_one_bridge_and_expire() -> None:
    token = sign_bridge_token("s3cret", "BRG-1", 2_000)
    assert verify_bridge_token("s3cret", token, now=1_000) == "BRG-1"
    assert verify_bridge_token("s3cret", token, now=2_001) is None
    assert verify_bridge_token("other", token, now=1_000) is None
    assert verify_bridge_token("s3cret", token.replace("BRG-1", "BRG-2"), now=1_000) is None
    # Without a secret no Bridge can connect, whatever it sends.
    assert verify_bridge_token("", sign_bridge_token("", "BRG-1", 2_000), now=1_000) is None


def test_a_crash_in_carla_keeps_the_scenario() -> None:
    bridges = FakeBridges(CRASH)
    result = generate(request(validator_bridge_uid="BRG-1"), ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), FakeBackend(),
                      CarlaCheck(bridges, "optional", 90))
    assert result.status == "completed"
    scenario = result.scenarios[0]
    assert bridges.calls == [("BRG-1", scenario.xosc_sha256, 90)]
    assert scenario.carla_validation["collision"] is True and scenario.carla_validation["collision_count"] == 1


def test_no_crash_in_carla_goes_back_to_the_llm() -> None:
    llm = ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)], [(SUBMIT_TOOL, VALID)]])
    result = generate(request(validator_bridge_uid="BRG-1"), llm, FakeBackend(), CarlaCheck(FakeBridges(MISS, CRASH), "optional", 90))
    assert result.status == "completed" and len(llm.sent) == 3
    [feedback] = tool_answers(llm, 3)
    assert feedback["valid"] is False and [issue["code"] for issue in feedback["issues"]] == ["NO_COLLISION_IN_CARLA"]


def test_no_crash_until_the_last_attempt_fails_the_run() -> None:
    llm = ScriptedLLM([READS, *[[(SUBMIT_TOOL, VALID)]] * 5])
    result = generate(request(validator_bridge_uid="BRG-1"), llm, FakeBackend(), CarlaCheck(FakeBridges(*[MISS] * 5), "optional", 90))
    assert result.status == "failed" and result.map_failures[0].code == "NO_VALID_PLAN"
    assert "NO_COLLISION_IN_CARLA" in result.map_failures[0].message


def test_without_a_verdict_optional_saves_and_required_fails() -> None:
    backend = FakeBackend()
    kept = generate(request(validator_bridge_uid="BRG-1"), ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), backend,
                    CarlaCheck(FakeBridges(AWAY), "optional", 90))
    assert kept.status == "completed" and kept.scenarios[0].carla_validation["code"] == "BRIDGE_NOT_CONNECTED"
    failed = generate(request(validator_bridge_uid="BRG-1"), ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), FakeBackend(),
                      CarlaCheck(FakeBridges(AWAY), "required", 90))
    assert failed.status == "failed" and failed.map_failures[0].code == "CARLA_VALIDATION_UNAVAILABLE"
    unpaired = generate(request(), ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), FakeBackend(),
                        CarlaCheck(FakeBridges(), "required", 90))
    assert unpaired.status == "failed" and unpaired.map_failures[0].code == "CARLA_VALIDATION_UNAVAILABLE"


def test_a_bridge_socket_answers_validate_requests(monkeypatch) -> None:
    from app import api

    class Settings:
        bridge_agent_secret = SecretStr("s3cret")

    registry = BridgeRegistry()
    monkeypatch.setattr(api, "get_settings", Settings)
    monkeypatch.setattr(api, "BRIDGES", registry)
    token = sign_bridge_token("s3cret", "BRG-1", 4_000_000_000)
    with TestClient(api.app) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            client.websocket_connect("/v1/bridge/ws", headers={"Authorization": "Bearer bad"}).__enter__()
        assert refused.value.code == 1008
        with client.websocket_connect("/v1/bridge/ws", headers={"Authorization": f"Bearer {token}"}) as bridge:
            assert bridge.receive_json() == {"type": "agent.welcome", "bridge_uid": "BRG-1"}
            verdicts: list[CarlaVerdict] = []
            worker = threading.Thread(target=lambda: verdicts.append(registry.validate("BRG-1", "<OpenSCENARIO/>", "ab", 30)))
            worker.start()
            asked = bridge.receive_json()
            assert asked["type"] == "validate.request" and asked["xosc"] == "<OpenSCENARIO/>" and asked["timeout_s"] == 30
            bridge.send_json({"type": "validate.result", "request_id": asked["request_id"], "status": "ok",
                              "collision": True, "collision_count": 2, "scenario_duration_s": 4.5, "wall_time_ms": 9000})
            worker.join(5)
    assert verdicts == [CarlaVerdict(ran=True, collision=True, collision_count=2, scenario_duration_s=4.5, wall_time_ms=9000)]
    assert registry.validate("BRG-1", "x", "y", 1).code == "BRIDGE_NOT_CONNECTED"
