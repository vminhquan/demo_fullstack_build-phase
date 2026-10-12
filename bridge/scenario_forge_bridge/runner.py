"""Simulator Runner: run the test cases of a run.assign on this machine's CARLA with ScenarioRunner.

Contract: docs/21-simulator-runner-bridge.md. Every test case runs in its own child process
(`<runner_python> scenario_runner.py --openscenario …`): the packaged Bridge has no `carla` package, and
ScenarioRunner calls sys.exit and keeps py_trees state, so it must not share the Bridge process.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from scenario_forge_bridge import config as config_store
from scenario_forge_bridge import runtime
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.config import BridgeConfig

Log = Callable[[str], None]
Send = Callable[[dict], Awaitable[None]]

# Who drives the ego in a test run: run.assign's `ads`, picked on the Test Suite screen (backend ADS_OPTIONS; None for
# older runs). Validation runs for agent2 always use NO_BRAIN: ScenarioRunner's default NpcVehicleControl keeps the
# lane and the scenario's speed and never reacts.
# ScenarioRunner v0.9.15/16 refuses --agent with --openscenario ("Agents are currently only compatible with route
# scenarios"), so an ADS is one of its OpenSCENARIO controllers, assigned to the ego in the Init (ControllerAction).
NO_BRAIN = "no-brain"
ADS_CONTROLLERS: dict[str, dict[str, str]] = {
    # srunner/scenariomanager/actorcontrols/simple_vehicle_control.py: follows the lane at the scenario's speed and
    # brakes for vehicles, bikes and pedestrians ahead (sensor.other.obstacle, forward channel only, no swerving).
    # Traffic lights are ignored: their phase is not part of the scenario and would make runs differ.
    "simple": {"module": "simple_vehicle_control", "consider_obstacles": "true", "max_deceleration": "8.0"},
}
ADS_CHOICES = (NO_BRAIN, *ADS_CONTROLLERS)

# Without criteria_* conditions in the StopTrigger, ScenarioRunner prints "Nothing to analyze" and passes.
CRITERIA = ("CollisionTest",)
# --timeout of scenario_runner.py: how long the CARLA client waits for the simulator (map loads are slow).
CARLA_CLIENT_TIMEOUT_S = 60
# Act end time when the XOSC gives none (seconds of simulation).
END_AFTER_S = 60
KILL_GRACE_S = 10
# While a scenario runs, log its elapsed time and last output line this often (shows where it is stuck).
PROGRESS_EVERY_S = 30
# ScenarioRunner prints this when it is done; after an exception its watchdog thread can keep the process alive.
EXIT_MARKER = "No more scenarios .... Exiting"
EXIT_GRACE_S = 15
POLL_S = 5
LOG_TAIL_CHARS = 3000

# Runs in the runner Python while a scenario plays: keeps the CARLA window camera on the ego ("hero" / "Ego"),
# and pulls back to frame both vehicles when another actor comes within 30 m. Exits when the ego is gone.
# The camera stays low (a few metres above the ego's road, looking almost level): from higher up it ended above
# the ceiling in tunnels and showed nothing.
CAMERA_CODE = r"""
import math, sys, time
import carla
client = carla.Client(sys.argv[1], int(sys.argv[2])); client.set_timeout(10)
seen, deadline = False, time.time() + 600
while time.time() < deadline:
    try:
        world = client.get_world()
        actors = list(world.get_actors().filter("vehicle.*")) + list(world.get_actors().filter("walker.*"))
    except RuntimeError:
        time.sleep(0.5); continue
    # ScenarioRunner names actors after their ScenarioObject: "hero", or "Ego" in older Scenario Forge files.
    hero = next((a for a in actors if a.attributes.get("role_name", "").lower() in ("hero", "ego", "ego_vehicle")), None)
    if hero is None:
        if seen:
            break
        time.sleep(0.2); continue
    seen = True
    h = hero.get_transform(); yaw = math.radians(h.rotation.yaw)
    others = [a for a in actors if a.id != hero.id]
    near = min(others, key=lambda a: a.get_location().distance(h.location), default=None)
    dist = near.get_location().distance(h.location) if near else 1e9
    if dist < 30:
        mid = (h.location + near.get_location()) * 0.5
        back = 7 + dist * 0.5
        loc = carla.Location(mid.x - back * math.cos(yaw), mid.y - back * math.sin(yaw), h.location.z + 2.8)
        rot = carla.Rotation(pitch=-10, yaw=h.rotation.yaw)
    else:
        loc = h.location + carla.Location(x=-8 * math.cos(yaw), y=-8 * math.sin(yaw), z=2.5)
        rot = carla.Rotation(pitch=-8, yaw=h.rotation.yaw)
    try:
        world.get_spectator().set_transform(carla.Transform(loc, rot))
    except RuntimeError:
        pass
    time.sleep(0.02)
"""


class CaseFailed(Exception):
    """A test case could not be run: reported as job.failed with this error code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code, self.message = code, message


@dataclass
class Outcome:
    verdict: str
    exit_code: int | None
    duration_ms: int
    metrics: dict


# ---------------------------------------------------------------- environment


def runner_problems(cfg: BridgeConfig) -> list[str]:
    """What is missing to run scenarios (empty list = ready). CARLA itself is checked per test case."""
    problems = []
    if not cfg.runner_python:
        problems.append("Chưa cài môi trường chạy test: chạy `scenario-forge-bridge setup-runner`")
    elif not Path(cfg.runner_python).is_file():
        problems.append(f"Không thấy runner_python: {cfg.runner_python}")
    if not cfg.runner_root:
        problems.append("Chưa có ScenarioRunner: chạy `scenario-forge-bridge setup-runner`")
    elif not Path(cfg.runner_root, "scenario_runner.py").is_file():
        problems.append(f"Không thấy scenario_runner.py trong {cfg.runner_root}")
    if not cfg.carla_root:
        problems.append("Chưa biết thư mục CARLA (cần PythonAPI/carla/agents): chạy `scenario-forge-bridge setup-runner --carla-root <thư mục>`")
    elif not Path(cfg.carla_root, "PythonAPI", "carla", "agents").is_dir():
        problems.append(f"Không thấy PythonAPI/carla/agents trong carla_root {cfg.carla_root}")
    return problems


def version_mismatch(cfg: BridgeConfig) -> str | None:
    """Message when the running CARLA server is not the version the runner was set up for (None = fine or unknown)."""
    if not cfg.carla_version or not cfg.runner_python:
        return None
    actual = runtime.server_version(cfg.runner_python, cfg.carla_host, cfg.carla_port, cfg.carla_root or None)
    if actual and actual != cfg.carla_version:
        return (f"Bridge được cài cho CARLA {cfg.carla_version} nhưng máy đang chạy CARLA {actual}. "
                f"Chạy lại `scenario-forge-bridge setup-runner` và chọn CARLA {actual}.")
    return None


def child_env(cfg: BridgeConfig) -> dict[str, str]:
    return runtime.runner_env(cfg.carla_root or None, cfg.runner_root)


# ---------------------------------------------------------------- XOSC and results


def prepare_xosc(xosc: str, names: tuple[str, ...] = CRITERIA, ads: str = NO_BRAIN) -> str:
    """Makes a Scenario Forge XOSC end and get judged under ScenarioRunner v0.9.16:

    - ScenarioRunner ignores the Storyboard StopTrigger except its criteria_* conditions (open_scenario.py:581);
      only an Act StopTrigger ends the scenario (open_scenario.py:492). Every Act without one gets the
      Storyboard's SimulationTimeCondition (else END_AFTER_S), otherwise a maneuver that never completes
      runs until the Bridge timeout. An Act also ends once its maneuvers are done, so each gets an event at its
      stop time that holds it open until then.
    - An Init RoutingAction keeps ScenarioRunner's init behavior RUNNING until the actor reaches the last waypoint,
      and the scenario cannot end before that (the "behavior" Parallel is SUCCESS_ON_ALL, open_scenario.py:513).
      Such routes move into the first Act as an event starting at once, so the Act StopTrigger cancels them.
    - ScenarioRunner reads RelativeTargetLane as lane changes from the actor's own lane (> 0 = left) and ignores
      entityRef (openscenario_parser.py:1383); "0" divides by zero. A "0" becomes ±1 toward the referenced entity.
    - criteria_<Name> conditions go into the Storyboard StopTrigger: without any, every run "passes".
    - `ads` other than NO_BRAIN: the ego gets that controller in the Init instead of any it had (ADS_CONTROLLERS).
    - ScenarioRunner builds those criteria for its ego vehicles only, and an ego is a Vehicle whose Property
      `type` is `ego_vehicle` (openscenario_configuration.py). With none, it prints "Nothing to analyze" and
      writes no report: the vehicle playing the hero (role_name hero, or named Ego) is marked as the ego.
    """
    root = ET.fromstring(xosc)
    _ensure_ego(root)
    storyboard = root.find("Storyboard")
    if storyboard is None:
        raise CaseFailed("INVALID_XOSC", "XOSC không có Storyboard")
    trigger = storyboard.find("StopTrigger")
    if trigger is None:
        trigger = ET.SubElement(storyboard, "StopTrigger")
    end_after = next((item.get("value") for item in trigger.iter("SimulationTimeCondition") if item.get("value")), None) or str(END_AFTER_S)

    _fix_lane_changes(storyboard)
    _assign_ads(root, storyboard, ads)

    first_act = next(storyboard.iter("Act"), None)
    init_actions = storyboard.find("Init/Actions")
    if first_act is not None and init_actions is not None:
        for private in init_actions.findall("Private"):
            for action in [item for item in private.findall("PrivateAction") if item.find("RoutingAction") is not None]:
                private.remove(action)
                _insert_route_event(first_act, private.get("entityRef", ""), action)

    for act in storyboard.iter("Act"):
        if act.find("StopTrigger") is None:
            # OpenSCENARIO 1.0: Act = ManeuverGroup+, StartTrigger, StopTrigger? (StopTrigger comes last).
            cond = ET.SubElement(ET.SubElement(ET.SubElement(act, "StopTrigger"), "ConditionGroup"), "Condition",
                                 {"name": "ActEndAfterTime", "delay": "0", "conditionEdge": "rising"})
            ET.SubElement(ET.SubElement(cond, "ByValueCondition"), "SimulationTimeCondition", {"value": end_after, "rule": "greaterThan"})
        _stop_on_ego_collision(root, act)
        _hold_until_stop(root, act)

    existing = {cond.get("name") for cond in trigger.iter("Condition")}
    missing = [name for name in names if f"criteria_{name}" not in existing]
    if missing:
        group = ET.SubElement(trigger, "ConditionGroup")
        for name in missing:
            cond = ET.SubElement(group, "Condition", {"name": f"criteria_{name}", "delay": "0", "conditionEdge": "rising"})
            ET.SubElement(ET.SubElement(cond, "ByValueCondition"), "ParameterCondition", {"parameterRef": "", "value": "", "rule": "lessThan"})
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _stop_on_ego_collision(root: ET.Element, act: ET.Element) -> None:
    """Give saved and imported XOSC the same collision end condition as newly generated scenarios."""
    ego = next((item.get("name") for item in root.findall("Entities/ScenarioObject")
                if any(prop.get("name") == "type" and prop.get("value") == "ego_vehicle"
                       for prop in item.iter("Property"))), None)
    stop = act.find("StopTrigger")
    if ego is None or stop is None:
        return
    for condition in stop.findall("./ConditionGroup/Condition"):
        collision = condition.find("./ByEntityCondition/EntityCondition/CollisionCondition")
        refs = condition.findall("./ByEntityCondition/TriggeringEntities/EntityRef")
        if collision is not None and len(collision) == 0 and any(ref.get("entityRef") == ego for ref in refs):
            return
    condition = ET.SubElement(ET.SubElement(stop, "ConditionGroup"), "Condition", {
        "name": "EndActOnCollision", "delay": "0", "conditionEdge": "rising",
    })
    by_entity = ET.SubElement(condition, "ByEntityCondition")
    ET.SubElement(ET.SubElement(by_entity, "TriggeringEntities", {"triggeringEntitiesRule": "any"}),
                  "EntityRef", {"entityRef": ego})
    ET.SubElement(ET.SubElement(by_entity, "EntityCondition"), "CollisionCondition")


def _hold_until_stop(root: ET.Element, act: ET.Element) -> None:
    """ScenarioRunner ends an Act once its maneuver groups are done, before the Act StopTrigger: a cut-in stopped
    as soon as the motorcycle changed lanes. A group whose event starts at the stop time keeps the Act running;
    its action re-sets the ego's initial speed (no effect)."""
    stop = act.find("StopTrigger")
    seconds = next((item.get("value") for item in stop.iter("SimulationTimeCondition") if item.get("value")), None) if stop is not None else None
    if seconds is None or any(event.get("name") == "HoldUntilEnd" for event in act.iter("Event")):
        return
    names = [item.get("name", "") for item in root.findall("Entities/ScenarioObject")]
    ego = next((name for name in names if name.lower() in ("hero", "ego", "ego_vehicle")), names[0] if names else None)
    if ego is None:
        return
    speed = next((node.get("value") for private in root.findall("Storyboard/Init/Actions/Private")
                  if private.get("entityRef") == ego for node in private.iter("AbsoluteTargetSpeed")), "0")
    group = ET.Element("ManeuverGroup", {"name": "Timeline", "maximumExecutionCount": "1"})
    ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": ego})
    event = ET.SubElement(ET.SubElement(group, "Maneuver", {"name": "HoldUntilEnd"}), "Event",
                          {"name": "HoldUntilEnd", "priority": "parallel"})
    speed_action = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(event, "Action", {"name": "KeepEgoSpeed"}),
                                                             "PrivateAction"), "LongitudinalAction"), "SpeedAction")
    ET.SubElement(speed_action, "SpeedActionDynamics", {"dynamicsShape": "step", "value": "0", "dynamicsDimension": "time"})
    ET.SubElement(ET.SubElement(speed_action, "SpeedActionTarget"), "AbsoluteTargetSpeed", {"value": speed})
    start = ET.SubElement(ET.SubElement(ET.SubElement(event, "StartTrigger"), "ConditionGroup"), "Condition",
                          {"name": "ScenarioEnd", "delay": "0", "conditionEdge": "rising"})
    ET.SubElement(ET.SubElement(start, "ByValueCondition"), "SimulationTimeCondition", {"value": seconds, "rule": "greaterThan"})
    # ManeuverGroups come before the Act's StartTrigger.
    children = list(act)
    position = next((index for index, child in enumerate(children) if child.tag == "StartTrigger"), len(children))
    act.insert(position, group)


def _ensure_ego(root: ET.Element) -> None:
    objects = root.findall("Entities/ScenarioObject")
    if any(prop.get("name") == "type" and prop.get("value") == "ego_vehicle"
           for item in objects for prop in item.iter("Property")):
        return

    def is_hero(item: ET.Element) -> bool:
        return any(prop.get("name") == "role_name" and prop.get("value") == "hero" for prop in item.iter("Property"))

    hero = next((item for item in objects if item.find("Vehicle") is not None and is_hero(item)), None)
    hero = hero or next((item for item in objects if item.find("Vehicle") is not None
                         and (item.get("name") or "").lower() in ("ego", "hero", "ego_vehicle")), None)
    if hero is None:
        return
    vehicle = hero.find("Vehicle")
    properties = vehicle.find("Properties")
    if properties is None:
        properties = ET.SubElement(vehicle, "Properties")
    kind = next((prop for prop in properties.findall("Property") if prop.get("name") == "type"), None)
    if kind is None:
        ET.SubElement(properties, "Property", {"name": "type", "value": "ego_vehicle"})
    else:
        kind.set("value", "ego_vehicle")


def _assign_ads(root: ET.Element, storyboard: ET.Element, ads: str) -> None:
    """Hand the ego to the ADS controller: a ControllerAction in its Init (ScenarioRunner reads it there), replacing
    any controller the XOSC assigned. NO_BRAIN leaves the XOSC as it is."""
    if ads == NO_BRAIN:
        return
    if ads not in ADS_CONTROLLERS:
        raise CaseFailed("UNKNOWN_ADS", f"Không có ADS '{ads}'. Chọn một trong: {', '.join(ADS_CHOICES)}")
    ego = next((item.get("name") for item in root.findall("Entities/ScenarioObject")
                if any(prop.get("name") == "type" and prop.get("value") == "ego_vehicle" for prop in item.iter("Property"))), None)
    actions = storyboard.find("Init/Actions")
    if ego is None or actions is None:
        raise CaseFailed("INVALID_XOSC", "XOSC không có xe ego hoặc Init để gắn ADS")
    private = next((item for item in actions.findall("Private") if item.get("entityRef") == ego), None)
    if private is None:
        private = ET.SubElement(actions, "Private", {"entityRef": ego})
    for action in [item for item in private.findall("PrivateAction") if item.find("ControllerAction") is not None]:
        private.remove(action)
    control = ET.Element("ControllerAction")
    controller = ET.SubElement(ET.SubElement(control, "AssignControllerAction"), "Controller", {"name": f"ScenarioForgeADS_{ads}"})
    properties = ET.SubElement(controller, "Properties")
    for name, value in ADS_CONTROLLERS[ads].items():
        ET.SubElement(properties, "Property", {"name": name, "value": value})
    # ScenarioRunner iterates this element unconditionally (openscenario_parser.get_controller): it must be present.
    override = ET.SubElement(control, "OverrideControllerValueAction")
    for tag in ("Throttle", "Brake", "Clutch", "ParkingBrake", "SteeringWheel"):
        ET.SubElement(override, tag, {"value": "0", "active": "false"})
    ET.SubElement(override, "Gear", {"number": "0", "active": "false"})
    action = ET.Element("PrivateAction")
    action.append(control)
    private.insert(0, action)


def _start_poses(storyboard: ET.Element) -> dict[str, tuple[float, float, float]]:
    """entity -> (x, y, h) of its Init TeleportAction WorldPosition (OpenSCENARIO frame)."""
    poses = {}
    for private in storyboard.findall("Init/Actions/Private"):
        position = private.find("PrivateAction/TeleportAction/Position/WorldPosition")
        if position is not None:
            poses[private.get("entityRef", "")] = tuple(float(position.get(key, 0) or 0) for key in ("x", "y", "h"))
    return poses


def _fix_lane_changes(storyboard: ET.Element) -> None:
    poses = _start_poses(storyboard)
    for group in storyboard.iter("ManeuverGroup"):
        mover = group.find("Actors/EntityRef")
        for target in group.iter("RelativeTargetLane"):
            if _number(target.get("value")) != 0:
                continue
            actor, reference = poses.get(mover.get("entityRef", "") if mover is not None else ""), poses.get(target.get("entityRef", ""))
            value = "1"  # left when the side is unknown: any non-zero value runs instead of crashing
            if actor and reference:
                # OpenSCENARIO is right-handed: the reference's left is (-sin h, cos h).
                dx, dy, heading = actor[0] - reference[0], actor[1] - reference[1], reference[2]
                left = dx * -math.sin(heading) + dy * math.cos(heading)
                value = "1" if left < 0 else "-1"
            target.set("value", value)


def _insert_route_event(act: ET.Element, entity: str, action: ET.Element) -> None:
    """ManeuverGroup (first in the Act, as OpenSCENARIO orders ManeuverGroup before StartTrigger) running `action` at once."""
    group = ET.Element("ManeuverGroup", {"maximumExecutionCount": "1", "name": f"MG_{entity}_route"})
    ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": entity})
    event = ET.SubElement(ET.SubElement(group, "Maneuver", {"name": f"Maneuver_{entity}_route"}), "Event",
                          {"name": f"Event_{entity}_route", "priority": "overwrite"})
    ET.SubElement(event, "Action", {"name": f"Action_{entity}_route"}).append(action)
    start = ET.SubElement(ET.SubElement(ET.SubElement(event, "StartTrigger"), "ConditionGroup"), "Condition",
                          {"name": f"{entity}RouteStart", "delay": "0", "conditionEdge": "rising"})
    ET.SubElement(ET.SubElement(start, "ByValueCondition"), "SimulationTimeCondition", {"value": "0", "rule": "greaterThan"})
    act.insert(0, group)


def parse_result(workdir: Path, exit_code: int | None, wall_ms: int) -> Outcome:
    """Reads the --json report of ScenarioRunner:
    {"scenario", "success", "criteria": [{"name", "actor", "optional", "expected", "actual", "success"}, …, {"name": "Duration", …}]}
    """
    reports = sorted(workdir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if "Traceback (most recent call last)" in log_tail(workdir):
        # An exception inside the scenario (e.g. an action ScenarioRunner cannot execute): any report is partial.
        raise CaseFailed("SCENARIO_RUNNER_ERROR", f"ScenarioRunner gặp lỗi khi chạy kịch bản (exit {exit_code}).\n{log_tail(workdir)}")
    if not reports:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", f"ScenarioRunner không tạo báo cáo (exit {exit_code}).\n{log_tail(workdir)}")
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    entries = data.get("criteria") or []
    criteria = [item for item in entries if item.get("name") != "Duration"]
    duration = next((item for item in entries if item.get("name") == "Duration"), None)
    collision_tests = [item for item in criteria if item.get("name") == "CollisionTest"]
    if not collision_tests:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", "Báo cáo ScenarioRunner thiếu CollisionTest nên không thể đánh giá kết quả.")
    try:
        counts = [float(item["actual"]) for item in collision_tests]
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", "CollisionTest không có số lần va chạm hợp lệ.") from exc
    if any(not math.isfinite(count) or count < 0 for count in counts):
        raise CaseFailed("SCENARIO_RUNNER_ERROR", "CollisionTest không có số lần va chạm hợp lệ.")
    collisions = sum(counts)
    scenario_s = _number(duration.get("actual")) if duration else None
    # This run judges ego safety by collision. Other ScenarioRunner criteria remain in metrics for diagnosis.
    # The exit code is not reliable (120 when stdout cannot be flushed).
    return Outcome(
        verdict="FAIL" if collisions > 0 else "PASS",
        exit_code=exit_code,
        duration_ms=int(scenario_s * 1000) if scenario_s else wall_ms,
        metrics={
            "collision": collisions > 0,
            "collision_count": int(collisions),
            "criteria": criteria,
            "scenario_duration_s": round(scenario_s, 3) if scenario_s else None,
            "wall_time_ms": wall_ms,
            "runner": "scenario_runner",
            "runner_success": bool(data.get("success")),
        },
    )


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def last_line(workdir: Path) -> str:
    try:
        lines = [line.strip() for line in log_tail(workdir).splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1][:200] if lines else "(chưa có output)"


def log_tail(workdir: Path) -> str:
    path = workdir / "runner.log"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-LOG_TAIL_CHARS:]


# ---------------------------------------------------------------- one test case


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_case(cfg: BridgeConfig, case: dict, workdir: Path, log: Log | None = None, ads: str = NO_BRAIN) -> Outcome:
    """Blocking: runs one test case (call it in a thread), the ego driven by `ads`. Raises CaseFailed when there is
    no result."""
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    scenario = workdir / f"{case.get('case_key') or case['test_case_id']}.xosc"
    scenario.write_text(prepare_xosc(case["xosc"], ads=ads), encoding="utf-8")
    env = child_env(cfg)

    camera = None
    if cfg.camera == "follow":
        try:
            with open(workdir / "camera.log", "wb") as camera_log:
                camera = subprocess.Popen([cfg.runner_python, "-c", CAMERA_CODE, cfg.carla_host, str(cfg.carla_port)],
                                          cwd=workdir, env=env, stdout=camera_log, stderr=subprocess.STDOUT)
        except OSError:
            camera = None  # the camera is a convenience: the test case runs without it

    command = [cfg.runner_python, "scenario_runner.py", "--openscenario", str(scenario),
               "--host", cfg.carla_host, "--port", str(cfg.carla_port), "--timeout", str(CARLA_CLIENT_TIMEOUT_S),
               "--json", "--outputDir", str(workdir)]
    started = time.monotonic()
    try:
        # Output goes to a file, never a pipe: a full or closed pipe hangs or breaks ScenarioRunner.
        with open(workdir / "runner.log", "wb") as runner_log:
            process = subprocess.Popen(command, cwd=cfg.runner_root, env=env, stdout=runner_log, stderr=subprocess.STDOUT)
            limit = int(case.get("timeout_s") or 300)
            exit_code, exited_at, next_progress = None, None, PROGRESS_EVERY_S
            while exit_code is None:
                elapsed = time.monotonic() - started
                if elapsed >= limit:
                    _stop(process)
                    raise CaseFailed("TIMEOUT", f"Quá {limit} giây.\n{log_tail(workdir)}")
                try:
                    exit_code = process.wait(timeout=min(POLL_S, limit - elapsed))
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - started
                    if exited_at is None and EXIT_MARKER in log_tail(workdir):
                        exited_at = elapsed
                    if exited_at is not None and elapsed - exited_at >= EXIT_GRACE_S:
                        # Done (usually after a Python exception) but not exiting: do not hold the queue until timeout.
                        _stop(process)
                        exit_code = process.returncode
                        break
                    if log and elapsed >= next_progress:
                        next_progress += PROGRESS_EVERY_S
                        log(f"    … {case.get('case_key')} đang chạy {int(elapsed)} s · {last_line(workdir)}")
    except OSError as exc:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", f"Không chạy được {cfg.runner_python}: {exc}") from exc
    finally:
        if camera is not None:
            _stop(camera)
    outcome = parse_result(workdir, exit_code, int((time.monotonic() - started) * 1000))
    outcome.metrics["ego_driver"] = ads
    return outcome


# ---------------------------------------------------------------- runs


class RunExecutor:
    """Queues run.assign messages and runs their test cases one at a time.

    Lives for the whole `run` command (not one socket session): a reconnect does not stop a running case,
    and `send` goes through whichever socket is open at that moment.
    """

    def __init__(self, cfg: BridgeConfig, send: Send, log: Log) -> None:
        self.cfg, self.send, self.log = cfg, send, log
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.seen: set[int] = set()
        # Reports not yet acknowledged by the backend, in send order: re-sent after every reconnect so a
        # dropped socket never loses a result (the backend handles repeats idempotently).
        self.unacked: dict[tuple, dict] = {}
        # One scenario at a time on this CARLA: test runs (queue) and agent2's validate.request share it.
        self.carla = asyncio.Lock()

    @staticmethod
    def _key(kind: str, message: dict) -> tuple:
        return kind, message.get("run_id"), message.get("test_case_id")

    async def report(self, message: dict) -> None:
        self.unacked[self._key(message["type"], message)] = message
        await self.send(message)

    def acknowledged(self, frame: dict) -> None:
        """ack (or a final error) from the backend for a report: stop re-sending it."""
        self.unacked.pop(self._key(str(frame.get("ref")), frame), None)

    async def resend_unacked(self) -> None:
        if self.unacked:
            self.log(f"Gửi lại {len(self.unacked)} kết quả chưa được máy chủ xác nhận.")
        for message in list(self.unacked.values()):
            await self.send(message)

    async def accept(self, message: dict) -> None:
        run_id = message["run_id"]
        if run_id in self.seen:
            # The backend re-sends run.assign until it sees run.accepted, and again on every reconnect.
            await self.send({"type": "run.accepted", "run_id": run_id})
            return
        self.seen.add(run_id)
        ads = message.get("ads") or NO_BRAIN
        if ads not in ADS_CHOICES:
            # A backend offering an ADS this Bridge does not have yet: update the Bridge.
            self.log(f"Không chạy được phiên {run_id}: Bridge này chưa có ADS '{ads}'.")
            await self.report({"type": "run.rejected", "run_id": run_id, "reason": "UNKNOWN_ADS",
                               "message": f"Bridge chưa có ADS '{ads}' (có: {', '.join(ADS_CHOICES)}). Cập nhật Bridge rồi chạy lại."})
            return
        problems = runner_problems(self.cfg)
        if problems:
            self.log("Không chạy được phiên " + str(run_id) + ": " + "; ".join(problems))
            await self.report({"type": "run.rejected", "run_id": run_id, "reason": "RUNNER_NOT_READY", "message": "\n".join(problems)[:4000]})
            return
        mismatch = await asyncio.to_thread(version_mismatch, self.cfg)
        if mismatch:
            self.log(f"Không chạy được phiên {run_id}: {mismatch}")
            await self.report({"type": "run.rejected", "run_id": run_id, "reason": "CARLA_VERSION_MISMATCH", "message": mismatch})
            return
        await self.send({"type": "run.accepted", "run_id": run_id})
        count = len(message.get("test_cases") or [])
        self.log(f"Nhận phiên chạy {run_id}: {count} test case · ego: {ads}"
                 + (f" (đợi {self.queue.qsize()} phiên trước)" if self.queue.qsize() else ""))
        await self.queue.put(message)

    async def serve(self) -> None:
        while True:
            message = await self.queue.get()
            try:
                await self._run(message)
            except Exception as exc:  # noqa: BLE001 - one broken run must not stop the queue
                self.log(f"Lỗi khi chạy phiên {message.get('run_id')}: {exc}")

    async def _run(self, message: dict) -> None:
        run_id = message["run_id"]
        ads = message.get("ads") or NO_BRAIN
        cases = message.get("test_cases") or []
        for index, case in enumerate(cases, 1):
            self.log(f"[{run_id}] {index}/{len(cases)} {case.get('case_key')} · {case.get('map_name')} · ego: {ads}")
            await self.report(await self._run_one(run_id, case, ads))
        await self.report({"type": "run.completed", "run_id": run_id})
        self.log(f"[{run_id}] Xong phiên chạy.")

    async def _run_one(self, run_id: int, case: dict, ads: str = NO_BRAIN) -> dict:
        base = {"run_id": run_id, "test_case_id": case["test_case_id"]}
        try:
            xosc = case.get("xosc") or ""
            if hashlib.sha256(xosc.encode("utf-8")).hexdigest() != case.get("xosc_sha256"):
                raise CaseFailed("XOSC_HASH_MISMATCH", "Nội dung XOSC nhận được không khớp xosc_sha256")
            async with self.carla:
                probe = await asyncio.to_thread(probe_carla, self.cfg.carla_host, self.cfg.carla_port)
                if not probe.reachable:
                    raise CaseFailed("CARLA_UNREACHABLE", f"Không kết nối được CARLA {probe.host}:{probe.port}: {probe.error}")
                await self.report({"type": "job.started", **base})
                workdir = config_store.config_dir() / "runs" / str(run_id) / str(case.get("case_key") or case["test_case_id"])
                outcome = await asyncio.to_thread(run_case, self.cfg, case, workdir, self.log, ads)
        except CaseFailed as exc:
            self.log(f"  ✗ {case.get('case_key')}: {exc.code}")
            return {"type": "job.failed", **base, "error_code": exc.code, "error_message": (exc.message or None) and exc.message[-4000:]}
        except Exception as exc:  # noqa: BLE001 - report instead of losing the case
            self.log(f"  ✗ {case.get('case_key')}: {exc}")
            return {"type": "job.failed", **base, "error_code": "BRIDGE_ERROR", "error_message": f"{type(exc).__name__}: {exc}"[:4000]}
        collision = f", {outcome.metrics['collision_count']} va chạm" if outcome.metrics["collision"] else ""
        self.log(f"  ✓ {case.get('case_key')}: {outcome.verdict}{collision} ({outcome.duration_ms / 1000:.1f} s, ego: {ads})")
        return {"type": "job.completed", **base, "revision": case["revision"], "xosc_sha256": case["xosc_sha256"],
                "map_name": case.get("map_name"), "verdict": outcome.verdict, "exit_code": outcome.exit_code,
                "duration_ms": outcome.duration_ms, "metrics": outcome.metrics}

    async def validate(self, message: dict) -> dict:
        """validate.request from agent2: run a just-generated XOSC once with the no-brain ego (it only keeps its lane
        and speed) and report whether it collided. Never raises: problems come back as status "error"."""
        request_id = str(message.get("request_id") or "")
        name = re.sub(r"[^A-Za-z0-9_-]", "", request_id)[:32] or "request"
        try:
            xosc = message.get("xosc") or ""
            if hashlib.sha256(xosc.encode("utf-8")).hexdigest() != message.get("xosc_sha256"):
                raise CaseFailed("XOSC_HASH_MISMATCH", "Nội dung XOSC nhận được không khớp xosc_sha256")
            problems = runner_problems(self.cfg)
            if problems:
                raise CaseFailed("RUNNER_NOT_READY", "; ".join(problems))
            if self.carla.locked():
                self.log(f"Kiểm tra kịch bản {name[:8]} cho agent2: đợi lượt chạy đang dùng CARLA…")
            async with self.carla:
                probe = await asyncio.to_thread(probe_carla, self.cfg.carla_host, self.cfg.carla_port)
                if not probe.reachable:
                    raise CaseFailed("CARLA_UNREACHABLE", f"Không kết nối được CARLA {probe.host}:{probe.port}: {probe.error}")
                self.log(f"Kiểm tra kịch bản {name[:8]} cho agent2 (ego không phản ứng)…")
                case = {"case_key": "validate", "test_case_id": name, "xosc": xosc, "timeout_s": message.get("timeout_s") or 120}
                # Validations run one at a time: one folder keeps the last run for diagnosis without piling up.
                workdir = config_store.config_dir() / "validations" / "latest"
                # Same camera setting as test runs (cfg.camera): the CARLA window follows the ego unless set to off.
                # Always the no-brain ego, whatever ADS test runs use: the scenario is valid only if it then collides.
                outcome = await asyncio.to_thread(run_case, self.cfg, case, workdir, self.log, NO_BRAIN)
        except CaseFailed as exc:
            self.log(f"  ✗ kiểm tra {name[:8]}: {exc.code}")
            return {"status": "error", "error_code": exc.code, "error_message": (exc.message or None) and exc.message[-4000:]}
        except Exception as exc:  # noqa: BLE001 - agent2 is waiting for an answer
            self.log(f"  ✗ kiểm tra {name[:8]}: {exc}")
            return {"status": "error", "error_code": "BRIDGE_ERROR", "error_message": f"{type(exc).__name__}: {exc}"[:4000]}
        collided = outcome.metrics["collision"]
        self.log(f"  ✓ kiểm tra {name[:8]}: {'va chạm' if collided else 'KHÔNG va chạm'} ({outcome.duration_ms / 1000:.1f} s)")
        return {"status": "ok", "collision": collided, "collision_count": outcome.metrics["collision_count"],
                "scenario_duration_s": outcome.metrics["scenario_duration_s"], "wall_time_ms": outcome.metrics["wall_time_ms"]}
