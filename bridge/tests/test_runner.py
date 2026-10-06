from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import textwrap
import time
import xml.etree.ElementTree as ET

import pytest

from scenario_forge_bridge import runner
from scenario_forge_bridge.carla_probe import CarlaProbe
from scenario_forge_bridge.config import BridgeConfig

XOSC = """<?xml version="1.0" encoding="utf-8"?>
<OpenSCENARIO><FileHeader revMajor="1" revMinor="0"/><RoadNetwork><LogicFile filepath="Town10HD_Opt"/></RoadNetwork>
<Storyboard><Story name="S"><Act name="A"><ManeuverGroup maximumExecutionCount="1" name="MG"><Actors selectTriggeringEntities="false"/></ManeuverGroup>
<StartTrigger><ConditionGroup><Condition name="ActStart" delay="0" conditionEdge="rising">
<ByValueCondition><SimulationTimeCondition value="0" rule="greaterThan"/></ByValueCondition></Condition></ConditionGroup></StartTrigger></Act></Story>
<StopTrigger><ConditionGroup>
<Condition name="EndSim" delay="0" conditionEdge="rising">
<ByValueCondition><SimulationTimeCondition value="20.0" rule="greaterThan"/></ByValueCondition></Condition>
</ConditionGroup></StopTrigger></Storyboard></OpenSCENARIO>
"""

# Stand-in for scenario_runner.py v0.9.16: same CLI, writes the same --json report shape.
FAKE_SCENARIO_RUNNER = textwrap.dedent('''
    import argparse, json, os, pathlib, sys, time
    p = argparse.ArgumentParser()
    for name in ("--openscenario", "--host", "--port", "--timeout", "--outputDir"):
        p.add_argument(name)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    xosc = pathlib.Path(a.openscenario).read_text()
    collisions = int(os.environ.get("FAKE_COLLISIONS", "0"))
    criteria = []
    if "criteria_CollisionTest" in xosc:
        criteria.append({"name": "CollisionTest", "actor": "tesla.model3-34", "optional": False,
                         "expected": 0, "actual": collisions, "success": collisions == 0})
    criteria.append({"name": "Duration", "actor": "all", "optional": False, "expected": 100000, "actual": 15.024, "success": True})
    report = {"scenario": "ScenarioForge: t", "success": all(c["success"] for c in criteria), "criteria": criteria}
    pathlib.Path(a.outputDir, "ScenarioForge: t.json").write_text(json.dumps(report))
    print("PYTHONPATH=" + os.environ.get("PYTHONPATH", ""))
    if os.environ.get("FAKE_HANG_AFTER_ERROR"):
        print("Traceback (most recent call last):")
        print("ZeroDivisionError: float division by zero")
        print("No more scenarios .... Exiting", flush=True)
        time.sleep(120)
    sys.exit(120)  # what a real run returned when stdout could not be flushed: must not decide the verdict
''')


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_BRIDGE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(runner, "probe_carla", lambda host, port: CarlaProbe(host, port, True))


@pytest.fixture()
def cfg(tmp_path) -> BridgeConfig:
    root = tmp_path / "scenario_runner"
    root.mkdir()
    (root / "scenario_runner.py").write_text(FAKE_SCENARIO_RUNNER)
    carla = tmp_path / "carla"
    (carla / "PythonAPI" / "carla" / "agents").mkdir(parents=True)
    return BridgeConfig(runner_python=sys.executable, runner_root=str(root), carla_root=str(carla), camera="off")


def case(**extra) -> dict:
    return {"test_case_id": 171, "case_key": "TC-000171", "revision": 2, "map_name": "Town10HD_Opt", "timeout_s": 30,
            "xosc_sha256": hashlib.sha256(XOSC.encode()).hexdigest(), "xosc": XOSC, **extra}


def test_prepare_xosc_adds_criteria_and_act_end_once() -> None:
    once = runner.prepare_xosc(XOSC)
    assert once.count('name="criteria_CollisionTest"') == 1
    assert 'parameterRef="" value="" rule="lessThan"' in once
    act = ET.fromstring(once).find("./Storyboard/Story/Act")
    assert [child.tag for child in act] == ["ManeuverGroup", "StartTrigger", "StopTrigger"]
    assert act.find("./StopTrigger//SimulationTimeCondition").get("value") == "20.0"
    twice = runner.prepare_xosc(once)
    assert twice.count('name="criteria_CollisionTest"') == 1 and twice.count('name="ActEndAfterTime"') == 1


def test_prepare_xosc_moves_init_routes_into_the_act() -> None:
    route = ('<Private entityRef="hero"><PrivateAction><TeleportAction/></PrivateAction><PrivateAction><RoutingAction><AssignRouteAction>'
             '<Route name="ego_route" closed="false"/></AssignRouteAction></RoutingAction></PrivateAction></Private>')
    xosc = XOSC.replace("<Storyboard>", f"<Storyboard><Init><Actions>{route}</Actions></Init>")
    root = ET.fromstring(runner.prepare_xosc(xosc))
    assert root.find("./Storyboard/Init//RoutingAction") is None
    assert root.find("./Storyboard/Init/Actions/Private/PrivateAction/TeleportAction") is not None
    act = root.find("./Storyboard/Story/Act")
    assert [child.get("name") or child.tag for child in act][:2] == ["MG_hero_route", "MG"]
    assert act.find("./ManeuverGroup/Maneuver/Event/Action/PrivateAction/RoutingAction/AssignRouteAction") is not None


def test_parse_result_reads_the_real_report_shape(tmp_path) -> None:
    (tmp_path / "r.json").write_text(json.dumps({
        "scenario": "ScenarioForge: sc_truck_sudden_brake", "success": False,
        "criteria": [{"name": "CollisionTest", "actor": "tesla.model3-34", "optional": False, "expected": 0, "actual": 2, "success": False},
                     {"name": "Duration", "actor": "all", "optional": False, "expected": 100000, "actual": 15.024313102476299, "success": True}],
    }))
    outcome = runner.parse_result(tmp_path, 120, 40000)
    assert outcome.verdict == "FAIL" and outcome.exit_code == 120 and outcome.duration_ms == 15024
    assert outcome.metrics["collision"] is True and outcome.metrics["collision_count"] == 2
    assert [item["name"] for item in outcome.metrics["criteria"]] == ["CollisionTest"]


def test_missing_report_is_a_runner_error(tmp_path) -> None:
    (tmp_path / "runner.log").write_text("The scenario cannot be loaded")
    with pytest.raises(runner.CaseFailed) as exc:
        runner.parse_result(tmp_path, 1, 10)
    assert exc.value.code == "SCENARIO_RUNNER_ERROR" and "cannot be loaded" in exc.value.message


def test_run_case_passes_criteria_and_agents_path(cfg, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_COLLISIONS", "1")
    outcome = runner.run_case(cfg, case(), tmp_path / "work")
    assert outcome.verdict == "FAIL" and outcome.metrics["collision_count"] == 1
    assert "PythonAPI" in (tmp_path / "work" / "runner.log").read_text()


def test_run_case_timeout(cfg, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_SLEEP", "5")
    with pytest.raises(runner.CaseFailed) as exc:
        runner.run_case(cfg, case(timeout_s=1), tmp_path / "work")
    assert exc.value.code == "TIMEOUT"


def run_executor(cfg: BridgeConfig, *messages: dict) -> list[dict]:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def main() -> None:
        executor = runner.RunExecutor(cfg, send, lambda _: None)
        serving = asyncio.create_task(executor.serve())
        for message in messages:
            await executor.accept(message)
        while not executor.queue.empty() or not any(m["type"] in ("run.completed", "run.rejected") for m in sent):
            await asyncio.sleep(0.05)
        serving.cancel()

    asyncio.run(asyncio.wait_for(main(), 30))
    return sent


def test_executor_reports_the_whole_run(cfg) -> None:
    assign = {"type": "run.assign", "run_id": 77, "project_id": 1, "test_cases": [case(), case(test_case_id=172, case_key="TC-000172", xosc_sha256="0" * 64)]}
    sent = run_executor(cfg, assign, assign)
    assert [m["type"] for m in sent] == ["run.accepted", "run.accepted", "job.started", "job.completed", "job.failed", "run.completed"]
    completed = sent[3]
    assert completed["verdict"] == "PASS" and completed["revision"] == 2 and completed["xosc_sha256"] == case()["xosc_sha256"]
    assert completed["map_name"] == "Town10HD_Opt" and completed["metrics"]["collision"] is False
    assert sent[4]["error_code"] == "XOSC_HASH_MISMATCH"


def test_executor_rejects_when_runner_is_not_configured() -> None:
    sent = run_executor(BridgeConfig(), {"type": "run.assign", "run_id": 5, "test_cases": [case()]})
    assert sent[0]["type"] == "run.rejected" and sent[0]["reason"] == "RUNNER_NOT_READY"


def test_carla_down_fails_the_case(cfg, monkeypatch) -> None:
    monkeypatch.setattr(runner, "probe_carla", lambda host, port: CarlaProbe(host, port, False, "refused"))
    sent = run_executor(cfg, {"type": "run.assign", "run_id": 9, "test_cases": [case()]})
    assert [m["type"] for m in sent] == ["run.accepted", "job.failed", "run.completed"]
    assert sent[1]["error_code"] == "CARLA_UNREACHABLE"


def test_reports_are_kept_until_acknowledged() -> None:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def main() -> None:
        executor = runner.RunExecutor(BridgeConfig(), send, lambda _: None)
        await executor.report({"type": "job.completed", "run_id": 7, "test_case_id": 1, "verdict": "PASS"})
        await executor.report({"type": "run.completed", "run_id": 7})
        executor.acknowledged({"type": "ack", "ref": "job.completed", "run_id": 7, "test_case_id": 1})
        sent.clear()
        await executor.resend_unacked()
        assert [m["type"] for m in sent] == ["run.completed"]
        executor.acknowledged({"type": "error", "ref": "run.completed", "code": "RUN_NOT_FOUND", "run_id": 7})
        assert executor.unacked == {}

    asyncio.run(main())


def test_prepare_xosc_turns_lane_change_zero_toward_the_ego() -> None:
    # Ego heading +x (h=0); in OpenSCENARIO's right-handed frame y < 0 is the ego's right.
    def scenario(actor_y: float) -> str:
        init = (f'<Init><Actions><Private entityRef="hero"><PrivateAction><TeleportAction><Position><WorldPosition x="0" y="0" z="0" h="0"/>'
                f'</Position></TeleportAction></PrivateAction></Private><Private entityRef="adversary_0"><PrivateAction><TeleportAction><Position>'
                f'<WorldPosition x="20" y="{actor_y}" z="0" h="0"/></Position></TeleportAction></PrivateAction></Private></Actions></Init>')
        group = ('<ManeuverGroup maximumExecutionCount="1" name="MG_adversary_0"><Actors selectTriggeringEntities="false">'
                 '<EntityRef entityRef="adversary_0"/></Actors><Maneuver name="M"><Event name="E" priority="overwrite"><Action name="A">'
                 '<PrivateAction><LateralAction><LaneChangeAction><LaneChangeActionDynamics dynamicsShape="sinusoidal" value="25" '
                 'dynamicsDimension="distance"/><LaneChangeTarget><RelativeTargetLane entityRef="hero" value="0"/></LaneChangeTarget>'
                 '</LaneChangeAction></LateralAction></PrivateAction></Action></Event></Maneuver></ManeuverGroup>')
        return XOSC.replace("<Storyboard>", f"<Storyboard>{init}").replace('<Act name="A">', f'<Act name="A">{group}')

    def value(xosc: str) -> str:
        return ET.fromstring(runner.prepare_xosc(xosc)).find(".//RelativeTargetLane").get("value")

    assert value(scenario(-3.5)) == "1"   # on the ego's right: change left
    assert value(scenario(3.5)) == "-1"   # on the ego's left: change right


def test_runner_error_that_does_not_exit_is_reported_quickly(cfg, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_HANG_AFTER_ERROR", "1")
    monkeypatch.setattr(runner, "EXIT_GRACE_S", 1)
    monkeypatch.setattr(runner, "POLL_S", 0.5)
    started = time.monotonic()
    with pytest.raises(runner.CaseFailed) as exc:
        runner.run_case(cfg, case(timeout_s=60), tmp_path / "work")
    assert exc.value.code == "SCENARIO_RUNNER_ERROR" and "ZeroDivisionError" in exc.value.message
    assert time.monotonic() - started < 15
