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
    report_name = "ScenarioForge: t.json" if sys.platform != "win32" else "ScenarioForge_t.json"
    pathlib.Path(a.outputDir, report_name).write_text(json.dumps(report))
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


def test_prepare_xosc_stops_existing_case_on_ego_collision_once() -> None:
    ego = ('<Entities><ScenarioObject name="hero"><Vehicle><Properties>'
           '<Property name="type" value="ego_vehicle"/></Properties></Vehicle></ScenarioObject></Entities>')
    source = XOSC.replace("<Storyboard>", ego + "<Storyboard>")
    prepared = runner.prepare_xosc(source)
    twice = runner.prepare_xosc(prepared)
    for xml in (prepared, twice):
        act = ET.fromstring(xml).find("./Storyboard/Story/Act")
        collisions = act.findall("./StopTrigger/ConditionGroup/Condition/ByEntityCondition")
        assert len(collisions) == 1
        assert collisions[0].find("./TriggeringEntities/EntityRef").get("entityRef") == "hero"
        assert collisions[0].find("./EntityCondition/CollisionCondition") is not None
        assert act.find("./StopTrigger/ConditionGroup/Condition/ByValueCondition/SimulationTimeCondition") is not None


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


def test_no_collision_passes_even_when_another_runner_criterion_fails(tmp_path) -> None:
    (tmp_path / "r.json").write_text(json.dumps({
        "scenario": "cut-in", "success": False,
        "criteria": [
            {"name": "CollisionTest", "actual": 0, "success": True},
            {"name": "KeepLaneTest", "actual": 1, "success": False},
            {"name": "Duration", "actual": 10, "success": True},
        ],
    }))
    outcome = runner.parse_result(tmp_path, 0, 12000)
    assert outcome.verdict == "PASS" and outcome.metrics["collision"] is False
    assert outcome.metrics["runner_success"] is False
    assert any(item["name"] == "KeepLaneTest" for item in outcome.metrics["criteria"])


def test_report_without_collision_measurement_is_an_error(tmp_path) -> None:
    (tmp_path / "r.json").write_text(json.dumps({"success": False, "criteria": [{"name": "Duration", "actual": 10}]}))
    with pytest.raises(runner.CaseFailed, match="thiếu CollisionTest"):
        runner.parse_result(tmp_path, 0, 12000)


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


def test_prepare_xosc_marks_the_hero_as_scenario_runner_ego() -> None:
    xosc = """<OpenSCENARIO><Entities>
      <ScenarioObject name="Ego"><Vehicle name="vehicle.audi.a2" vehicleCategory="car"><Properties>
        <Property name="type" value="car"/><Property name="role_name" value="hero"/></Properties></Vehicle></ScenarioObject>
      <ScenarioObject name="Motorcycle"><Vehicle name="vehicle.yamaha.yzf" vehicleCategory="motorbike"><Properties>
        <Property name="type" value="motorcycle"/></Properties></Vehicle></ScenarioObject>
    </Entities><Storyboard><Init><Actions/></Init></Storyboard></OpenSCENARIO>"""
    root = ET.fromstring(runner.prepare_xosc(xosc))
    types = {item.get("name"): item.find("Vehicle/Properties/Property[@name='type']").get("value")
             for item in root.findall("Entities/ScenarioObject")}
    assert types == {"Ego": "ego_vehicle", "Motorcycle": "motorcycle"}
    # A file that already names its ego is left alone.
    assert runner.prepare_xosc(runner.prepare_xosc(xosc)).count('value="ego_vehicle"') == 1


def test_prepare_xosc_holds_the_act_open_until_its_stop_time() -> None:
    xosc = """<OpenSCENARIO><Entities><ScenarioObject name="Ego"><Vehicle name="vehicle.audi.a2" vehicleCategory="car"/></ScenarioObject>
      <ScenarioObject name="Motorcycle"><Vehicle name="vehicle.yamaha.yzf" vehicleCategory="motorbike"/></ScenarioObject></Entities>
      <Storyboard><Init><Actions><Private entityRef="Ego"><PrivateAction><LongitudinalAction><SpeedAction>
        <SpeedActionDynamics dynamicsShape="step" value="0" dynamicsDimension="time"/>
        <SpeedActionTarget><AbsoluteTargetSpeed value="10.5"/></SpeedActionTarget></SpeedAction></LongitudinalAction></PrivateAction></Private>
      </Actions></Init>
      <Story name="S"><Act name="A"><ManeuverGroup name="MotorcycleManeuver" maximumExecutionCount="1"/>
        <StartTrigger/><StopTrigger><ConditionGroup><Condition name="EndAct" delay="0" conditionEdge="rising">
          <ByValueCondition><SimulationTimeCondition value="5.6" rule="greaterThan"/></ByValueCondition></Condition></ConditionGroup></StopTrigger>
      </Act></Story><StopTrigger/></Storyboard></OpenSCENARIO>"""
    once = runner.prepare_xosc(xosc)
    act = ET.fromstring(once).find("Storyboard/Story/Act")
    assert [child.tag for child in act] == ["ManeuverGroup", "ManeuverGroup", "StartTrigger", "StopTrigger"]
    hold = act.findall("ManeuverGroup")[1]
    assert hold.find(".//EntityRef").get("entityRef") == "Ego"
    assert hold.find(".//SimulationTimeCondition").get("value") == "5.6"
    assert hold.find(".//AbsoluteTargetSpeed").get("value") == "10.5"
    assert runner.prepare_xosc(once).count("HoldUntilEnd") == once.count("HoldUntilEnd")  # added once


def validate(cfg: BridgeConfig, message: dict) -> dict:
    async def main() -> dict:
        return await runner.RunExecutor(cfg, lambda _: asyncio.sleep(0), lambda _: None).validate(message)

    return asyncio.run(asyncio.wait_for(main(), 30))


def validate_request(**extra) -> dict:
    return {"type": "validate.request", "request_id": "ab12", "xosc": XOSC,
            "xosc_sha256": hashlib.sha256(XOSC.encode()).hexdigest(), "timeout_s": 30, **extra}


def test_validate_reports_whether_the_no_brain_ego_collided(cfg, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_COLLISIONS", "1")
    assert validate(cfg, validate_request()) == {"status": "ok", "collision": True, "collision_count": 1,
                                                  "scenario_duration_s": 15.024, "wall_time_ms": pytest.approx(0, abs=30_000)}
    monkeypatch.setenv("FAKE_COLLISIONS", "0")
    assert validate(cfg, validate_request())["collision"] is False


def test_validate_answers_errors_instead_of_raising(cfg, monkeypatch) -> None:
    assert validate(cfg, validate_request(xosc_sha256="0" * 64))["error_code"] == "XOSC_HASH_MISMATCH"
    assert validate(BridgeConfig(), validate_request())["error_code"] == "RUNNER_NOT_READY"
    monkeypatch.setattr(runner, "probe_carla", lambda host, port: CarlaProbe(host, port, False, "refused"))
    answer = validate(cfg, validate_request())
    assert answer["status"] == "error" and answer["error_code"] == "CARLA_UNREACHABLE"


def test_validate_waits_for_a_test_run_holding_carla(cfg) -> None:
    async def main() -> list[str]:
        executor = runner.RunExecutor(cfg, lambda _: asyncio.sleep(0), lambda _: None)
        order: list[str] = []
        async with executor.carla:
            checking = asyncio.create_task(executor.validate(validate_request()))
            await asyncio.sleep(0.2)
            order.append("run done" if not checking.done() else "validated too early")
        await checking
        order.append("validated")
        return order

    assert asyncio.run(asyncio.wait_for(main(), 30)) == ["run done", "validated"]


# XOSC with an ego and its Init, as agent2 renders them (the minimal XOSC above has neither).
EGO_XOSC = XOSC.replace("<RoadNetwork>", """<Entities>
  <ScenarioObject name="hero"><Vehicle name="vehicle.audi.a2" vehicleCategory="car"><Properties>
    <Property name="type" value="ego_vehicle"/><Property name="role_name" value="hero"/></Properties></Vehicle></ScenarioObject>
  <ScenarioObject name="moto1"><Vehicle name="vehicle.yamaha.yzf" vehicleCategory="motorbike"/></ScenarioObject>
</Entities><RoadNetwork>""").replace("<Story ", """<Init><Actions><Private entityRef="hero"><PrivateAction><LongitudinalAction><SpeedAction>
  <SpeedActionDynamics dynamicsShape="step" value="0" dynamicsDimension="time"/>
  <SpeedActionTarget><AbsoluteTargetSpeed value="13.9"/></SpeedActionTarget></SpeedAction></LongitudinalAction></PrivateAction></Private>
</Actions></Init><Story """)


def ego_controller(xosc: str) -> dict[str, str] | None:
    """module and arguments of the ControllerAction in the ego's Init, as ScenarioRunner's get_controller reads them."""
    root = ET.fromstring(xosc)
    actions = root.findall("Storyboard/Init/Actions/Private[@entityRef='hero']/PrivateAction/ControllerAction")
    if not actions:
        return None
    assert len(actions) == 1
    # get_controller iterates OverrideControllerValueAction unconditionally and rejects any active override.
    assert all(child.get("active") == "false" for child in actions[0].find("OverrideControllerValueAction"))
    properties = actions[0].find("AssignControllerAction/Controller/Properties")
    return {item.get("name"): item.get("value") for item in properties}


def test_no_brain_leaves_the_ego_to_scenario_runner_default() -> None:
    assert ego_controller(runner.prepare_xosc(EGO_XOSC)) is None


def test_an_ads_takes_the_ego_in_the_init_replacing_any_controller() -> None:
    once = runner.prepare_xosc(EGO_XOSC, ads="simple")
    assert ego_controller(once) == {"module": "simple_vehicle_control", "consider_obstacles": "true", "max_deceleration": "8.0"}
    # The Init speed stays: the controller cruises at the scenario's speed, so the incident timing holds.
    assert ET.fromstring(once).find(".//Private[@entityRef='hero']//AbsoluteTargetSpeed").get("value") == "13.9"
    # Run twice (or on a file that had its own controller): still one controller, the ADS.
    assert ego_controller(runner.prepare_xosc(once, ads="simple"))["module"] == "simple_vehicle_control"
    with pytest.raises(runner.CaseFailed) as unknown:
        runner.prepare_xosc(EGO_XOSC, ads="autoware")
    assert unknown.value.code == "UNKNOWN_ADS"


def test_test_runs_use_the_ads_and_validations_the_no_brain_ego(cfg, tmp_path) -> None:
    digest = hashlib.sha256(EGO_XOSC.encode()).hexdigest()
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def main() -> dict:
        executor = runner.RunExecutor(cfg, send, lambda _: None, ads="simple")
        serving = asyncio.create_task(executor.serve())
        await executor.accept({"type": "run.assign", "run_id": 3, "test_cases": [case(xosc=EGO_XOSC, xosc_sha256=digest)]})
        while not any(m["type"] == "run.completed" for m in sent):
            await asyncio.sleep(0.05)
        serving.cancel()
        return await executor.validate(validate_request(xosc=EGO_XOSC, xosc_sha256=digest))

    checked = asyncio.run(asyncio.wait_for(main(), 30))
    ran = (tmp_path / "config" / "runs" / "3" / "TC-000171" / "TC-000171.xosc").read_text()
    validated = (tmp_path / "config" / "validations" / "latest" / "validate.xosc").read_text()
    assert ego_controller(ran)["module"] == "simple_vehicle_control" and ego_controller(validated) is None
    completed = next(m for m in sent if m["type"] == "job.completed")
    assert completed["metrics"]["ego_driver"] == "simple" and checked["status"] == "ok"
