"""Simulator Runner: run the test cases of a run.assign on this machine's CARLA with ScenarioRunner.

Contract: docs/21-simulator-runner-bridge.md. Every test case runs in its own child process
(`<runner_python> scenario_runner.py --openscenario …`): the packaged Bridge has no `carla` package, and
ScenarioRunner calls sys.exit and keeps py_trees state, so it must not share the Bridge process.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.config import BridgeConfig

Log = Callable[[str], None]
Send = Callable[[dict], Awaitable[None]]

# Without criteria_* conditions in the StopTrigger, ScenarioRunner prints "Nothing to analyze" and passes.
CRITERIA = ("CollisionTest",)
# --timeout of scenario_runner.py: how long the CARLA client waits for the simulator (map loads are slow).
CARLA_CLIENT_TIMEOUT_S = 60
# Act end time when the XOSC gives none (seconds of simulation).
END_AFTER_S = 60
KILL_GRACE_S = 10
# While a scenario runs, log its elapsed time and last output line this often (shows where it is stuck).
PROGRESS_EVERY_S = 30
LOG_TAIL_CHARS = 3000

# Runs in the runner Python while a scenario plays: keeps the CARLA window camera on the ego ("hero"),
# and pulls back to frame both vehicles when another actor comes within 30 m. Exits when the ego is gone.
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
    hero = next((a for a in actors if a.attributes.get("role_name") == "hero"), None)
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
        back = 10 + dist * 0.6
        loc = carla.Location(mid.x - back * math.cos(yaw), mid.y - back * math.sin(yaw), mid.z + 6 + dist * 0.4)
        rot = carla.Rotation(pitch=-30, yaw=h.rotation.yaw)
    else:
        loc = h.location + carla.Location(x=-10 * math.cos(yaw), y=-10 * math.sin(yaw), z=5)
        rot = carla.Rotation(pitch=-15, yaw=h.rotation.yaw)
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
        problems.append("Chưa cấu hình runner_python (sfbridge config --runner-python <python có carla + ScenarioRunner>)")
    elif not Path(cfg.runner_python).is_file():
        problems.append(f"Không thấy runner_python: {cfg.runner_python}")
    if not cfg.runner_root:
        problems.append("Chưa cấu hình runner_root (sfbridge config --runner-root <thư mục scenario_runner>)")
    elif not Path(cfg.runner_root, "scenario_runner.py").is_file():
        problems.append(f"Không thấy scenario_runner.py trong {cfg.runner_root}")
    if cfg.carla_root and not Path(cfg.carla_root, "PythonAPI", "carla", "agents").is_dir():
        problems.append(f"Không thấy PythonAPI/carla/agents trong carla_root {cfg.carla_root}")
    return problems


def child_env(cfg: BridgeConfig) -> dict[str, str]:
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # PyInstaller points LD_LIBRARY_PATH at its own bundle; the runner Python needs the system one.
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    if cfg.carla_root:
        # ScenarioRunner imports `agents.navigation…`, which ships in CARLA's PythonAPI/carla folder.
        agents = str(Path(cfg.carla_root, "PythonAPI", "carla"))
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [agents, env.get("PYTHONPATH")]))
        env["CARLA_ROOT"] = cfg.carla_root
    env["SCENARIO_RUNNER_ROOT"] = cfg.runner_root
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ---------------------------------------------------------------- XOSC and results


def prepare_xosc(xosc: str, names: tuple[str, ...] = CRITERIA) -> str:
    """Makes a Scenario Forge XOSC end and get judged under ScenarioRunner v0.9.16:

    - ScenarioRunner ignores the Storyboard StopTrigger except its criteria_* conditions (open_scenario.py:581);
      only an Act StopTrigger ends the scenario (open_scenario.py:492). Every Act without one gets the
      Storyboard's SimulationTimeCondition (else END_AFTER_S), otherwise a maneuver that never completes
      runs until the Bridge timeout.
    - criteria_<Name> conditions go into the Storyboard StopTrigger: without any, every run "passes".
    """
    root = ET.fromstring(xosc)
    storyboard = root.find("Storyboard")
    if storyboard is None:
        raise CaseFailed("INVALID_XOSC", "XOSC không có Storyboard")
    trigger = storyboard.find("StopTrigger")
    if trigger is None:
        trigger = ET.SubElement(storyboard, "StopTrigger")
    end_after = next((item.get("value") for item in trigger.iter("SimulationTimeCondition") if item.get("value")), None) or str(END_AFTER_S)

    for act in storyboard.iter("Act"):
        if act.find("StopTrigger") is not None:
            continue
        # OpenSCENARIO 1.0: Act = ManeuverGroup+, StartTrigger, StopTrigger? (StopTrigger comes last).
        cond = ET.SubElement(ET.SubElement(ET.SubElement(act, "StopTrigger"), "ConditionGroup"), "Condition",
                             {"name": "ActEndAfterTime", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(cond, "ByValueCondition"), "SimulationTimeCondition", {"value": end_after, "rule": "greaterThan"})

    existing = {cond.get("name") for cond in trigger.iter("Condition")}
    missing = [name for name in names if f"criteria_{name}" not in existing]
    if missing:
        group = ET.SubElement(trigger, "ConditionGroup")
        for name in missing:
            cond = ET.SubElement(group, "Condition", {"name": f"criteria_{name}", "delay": "0", "conditionEdge": "rising"})
            ET.SubElement(ET.SubElement(cond, "ByValueCondition"), "ParameterCondition", {"parameterRef": "", "value": "", "rule": "lessThan"})
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def parse_result(workdir: Path, exit_code: int | None, wall_ms: int) -> Outcome:
    """Reads the --json report of ScenarioRunner:
    {"scenario", "success", "criteria": [{"name", "actor", "optional", "expected", "actual", "success"}, …, {"name": "Duration", …}]}
    """
    reports = sorted(workdir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", f"ScenarioRunner không tạo báo cáo (exit {exit_code}).\n{log_tail(workdir)}")
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    entries = data.get("criteria") or []
    criteria = [item for item in entries if item.get("name") != "Duration"]
    duration = next((item for item in entries if item.get("name") == "Duration"), None)
    collisions = sum(_number(item.get("actual")) for item in criteria if item.get("name") == "CollisionTest")
    scenario_s = _number(duration.get("actual")) if duration else None
    # The exit code is not reliable (120 when stdout cannot be flushed): the report decides the verdict.
    return Outcome(
        verdict="PASS" if data.get("success") else "FAIL",
        exit_code=exit_code,
        duration_ms=int(scenario_s * 1000) if scenario_s else wall_ms,
        metrics={
            "collision": collisions > 0,
            "collision_count": int(collisions),
            "criteria": criteria,
            "scenario_duration_s": round(scenario_s, 3) if scenario_s else None,
            "wall_time_ms": wall_ms,
            "runner": "scenario_runner",
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


def run_case(cfg: BridgeConfig, case: dict, workdir: Path, log: Log | None = None) -> Outcome:
    """Blocking: runs one test case (call it in a thread). Raises CaseFailed when there is no result."""
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    scenario = workdir / f"{case.get('case_key') or case['test_case_id']}.xosc"
    scenario.write_text(prepare_xosc(case["xosc"]), encoding="utf-8")
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
            exit_code = None
            while exit_code is None:
                elapsed = time.monotonic() - started
                if elapsed >= limit:
                    _stop(process)
                    raise CaseFailed("TIMEOUT", f"Quá {limit} giây.\n{log_tail(workdir)}")
                try:
                    exit_code = process.wait(timeout=min(PROGRESS_EVERY_S, limit - elapsed))
                except subprocess.TimeoutExpired:
                    if log:
                        log(f"    … {case.get('case_key')} đang chạy {int(time.monotonic() - started)} s · {last_line(workdir)}")
    except OSError as exc:
        raise CaseFailed("SCENARIO_RUNNER_ERROR", f"Không chạy được {cfg.runner_python}: {exc}") from exc
    finally:
        if camera is not None:
            _stop(camera)
    return parse_result(workdir, exit_code, int((time.monotonic() - started) * 1000))


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
        problems = runner_problems(self.cfg)
        if problems:
            self.log("Không chạy được phiên " + str(run_id) + ": " + "; ".join(problems))
            await self.report({"type": "run.rejected", "run_id": run_id, "reason": "RUNNER_NOT_READY", "message": "\n".join(problems)[:4000]})
            return
        await self.send({"type": "run.accepted", "run_id": run_id})
        count = len(message.get("test_cases") or [])
        self.log(f"Nhận phiên chạy {run_id}: {count} test case" + (f" (đợi {self.queue.qsize()} phiên trước)" if self.queue.qsize() else ""))
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
        cases = message.get("test_cases") or []
        for index, case in enumerate(cases, 1):
            self.log(f"[{run_id}] {index}/{len(cases)} {case.get('case_key')} · {case.get('map_name')}")
            await self.report(await self._run_one(run_id, case))
        await self.report({"type": "run.completed", "run_id": run_id})
        self.log(f"[{run_id}] Xong phiên chạy.")

    async def _run_one(self, run_id: int, case: dict) -> dict:
        base = {"run_id": run_id, "test_case_id": case["test_case_id"]}
        try:
            xosc = case.get("xosc") or ""
            if hashlib.sha256(xosc.encode("utf-8")).hexdigest() != case.get("xosc_sha256"):
                raise CaseFailed("XOSC_HASH_MISMATCH", "Nội dung XOSC nhận được không khớp xosc_sha256")
            probe = await asyncio.to_thread(probe_carla, self.cfg.carla_host, self.cfg.carla_port)
            if not probe.reachable:
                raise CaseFailed("CARLA_UNREACHABLE", f"Không kết nối được CARLA {probe.host}:{probe.port}: {probe.error}")
            await self.report({"type": "job.started", **base})
            workdir = config_store.config_dir() / "runs" / str(run_id) / str(case.get("case_key") or case["test_case_id"])
            outcome = await asyncio.to_thread(run_case, self.cfg, case, workdir, self.log)
        except CaseFailed as exc:
            self.log(f"  ✗ {case.get('case_key')}: {exc.code}")
            return {"type": "job.failed", **base, "error_code": exc.code, "error_message": (exc.message or None) and exc.message[-4000:]}
        except Exception as exc:  # noqa: BLE001 - report instead of losing the case
            self.log(f"  ✗ {case.get('case_key')}: {exc}")
            return {"type": "job.failed", **base, "error_code": "BRIDGE_ERROR", "error_message": f"{type(exc).__name__}: {exc}"[:4000]}
        collision = f", {outcome.metrics['collision_count']} va chạm" if outcome.metrics["collision"] else ""
        self.log(f"  ✓ {case.get('case_key')}: {outcome.verdict}{collision} ({outcome.duration_ms / 1000:.1f} s)")
        return {"type": "job.completed", **base, "revision": case["revision"], "xosc_sha256": case["xosc_sha256"],
                "map_name": case.get("map_name"), "verdict": outcome.verdict, "exit_code": outcome.exit_code,
                "duration_ms": outcome.duration_ms, "metrics": outcome.metrics}
