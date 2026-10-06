"""Simulator Runner environment: one per CARLA version, created and owned by the Bridge.

<config dir>/runtime/<carla version>/
    venv/             Python picked for that CARLA (uv downloads it; the user's Python is never touched)
    scenario_runner/  ScenarioRunner at the matching tag, with the known bugs patched

The Bridge never imports `carla` itself: every CARLA call runs in this venv's Python (ScenarioRunner, the camera,
catalog sync), so the Bridge CLI works on any Python >= 3.10 whatever CARLA the user runs.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from scenario_forge_bridge import carla_catalog
from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_catalog import CarlaUnavailable, MapResult
from scenario_forge_bridge.config import BridgeConfig

Log = Callable[[str], None]


@dataclass(frozen=True)
class RunnerSpec:
    tag: str             # ScenarioRunner git tag
    python: str          # Python for the venv: the carla wheel and ScenarioRunner's pinned requirements must both exist for it
    patches: tuple[str, ...]


# Newest first: the menu lists them in this order. carla wheels: 0.9.16 cp310-312, 0.9.15 cp37-310;
# ScenarioRunner v0.9.15 pins numpy 1.18.4 / opencv 4.2.0.32 / Shapely 1.7.1 (wheels up to cp38).
RUNNERS: dict[str, RunnerSpec] = {
    "0.9.16": RunnerSpec(tag="v0.9.16", python="3.10", patches=("times_none", "self_reset")),
    "0.9.15": RunnerSpec(tag="v0.9.15", python="3.8", patches=("self_reset",)),
}

PATCHED_FILE = "srunner/scenariomanager/scenarioatomics/atomic_behaviors.py"
# (original, fixed) in PATCHED_FILE. times_none: an Init AssignRouteAction passes times=None and len(None) crashes.
# self_reset: a re-ticked ChangeActorControl resets its own controller, then run_step() hits a None planner.
PATCHES: dict[str, tuple[str, str]] = {
    "times_none": (
        "        if len(self._waypoints) != len(self._times):\n",
        "        if self._times is not None and len(self._waypoints) != len(self._times):\n",
    ),
    "self_reset": (
        "            if self._actor.id in actor_dict:\n                actor_dict[self._actor.id].reset()\n",
        "            if self._actor.id in actor_dict:\n                if actor_dict[self._actor.id] is not self._actor_control:\n"
        "                    actor_dict[self._actor.id].reset()\n",
    ),
}

SCENARIO_RUNNER_ZIP = "https://github.com/carla-simulator/scenario_runner/archive/refs/tags/{tag}.zip"
_VERSION_IN_WHEEL = re.compile(r"^carla-(\d+\.\d+\.\d+)-")
_VERSION_IN_FOLDER = re.compile(r"(\d+\.\d+\.\d+)")


class SetupFailed(Exception):
    """A step of the runner setup failed; the message says which and what to do."""


# ---------------------------------------------------------------- finding CARLA


def is_carla_root(path: Path) -> bool:
    # ScenarioRunner imports `agents`, shipped only in the CARLA folder (not in the pip package).
    return (path / "PythonAPI" / "carla" / "agents").is_dir()


def carla_version_of(root: Path) -> str | None:
    """Version of a CARLA install: from its bundled client wheels, else from the folder name (CARLA_0.9.16)."""
    dist = root / "PythonAPI" / "carla" / "dist"
    if dist.is_dir():
        for item in sorted(dist.iterdir()):
            match = _VERSION_IN_WHEEL.match(item.name)
            if match:
                return match.group(1)
    match = _VERSION_IN_FOLDER.search(root.name)
    return match.group(1) if match else None


def running_carla_root() -> Path | None:
    """Folder of the CarlaUE4 process that is running, if any."""
    executables: list[Path] = []
    if sys.platform.startswith("linux"):
        for exe in Path("/proc").glob("[0-9]*/exe"):
            try:
                target = Path(os.readlink(exe))
            except OSError:
                continue
            if target.name.startswith("CarlaUE4"):
                executables.append(target)
    elif sys.platform == "win32":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process CarlaUE4* -ErrorAction SilentlyContinue).Path"],
                                 capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        executables = [Path(line.strip()) for line in out.splitlines() if line.strip()]
    for exe in executables:
        for parent in exe.parents:
            if is_carla_root(parent):
                return parent
    return None


def find_carla_roots(explicit: str | None = None) -> list[Path]:
    """CARLA installs on this machine, most likely first: --carla-root, $CARLA_ROOT, the running one, usual folders."""
    home = Path.home()
    candidates: list[Path] = [Path(item).expanduser() for item in (explicit, os.environ.get("CARLA_ROOT")) if item]
    running = running_carla_root()
    if running:
        candidates.append(running)
    for pattern in ("CARLA*", "Carla*", "carla*"):
        for folder in (home, home / "Downloads", home / "Desktop", Path("/opt"), Path("C:/"), Path("D:/")):
            try:
                candidates.extend(sorted(folder.glob(pattern)))
            except OSError:
                continue
    roots: list[Path] = []
    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            continue
        if resolved not in roots and is_carla_root(resolved):
            roots.append(resolved)
    return roots


# ---------------------------------------------------------------- environment


def runtime_dir(version: str) -> Path:
    return config_store.config_dir() / "runtime" / version


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    for item in (home / ".local/bin/uv", home / ".cargo/bin/uv", home / ".local/bin/uv.exe", home / ".cargo/bin/uv.exe"):
        if item.is_file():
            return str(item)
    return None


def runner_env(carla_root: str | None, scenario_runner: str | None = None) -> dict[str, str]:
    """Environment for the runner Python: nothing inherited that could point it at another Python's packages."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # PyInstaller points LD_LIBRARY_PATH at its own bundle; the runner Python needs the system one.
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    if carla_root:
        env["PYTHONPATH"] = str(Path(carla_root, "PythonAPI", "carla"))  # the `agents` package
        env["CARLA_ROOT"] = carla_root
    if scenario_runner:
        env["SCENARIO_RUNNER_ROOT"] = scenario_runner
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _run(command: list[str], what: str, env: dict[str, str] | None = None, timeout: float = 1800) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupFailed(f"{what}: {exc}") from exc
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip()[-1500:]
        raise SetupFailed(f"{what} thất bại (mã {done.returncode}):\n{tail}")
    return done.stdout


def server_version(python: str, host: str, port: int, carla_root: str | None = None) -> str | None:
    """Version reported by the running CARLA server, asked through the runner Python (None when it cannot tell)."""
    code = "import sys, carla\nc = carla.Client(sys.argv[1], int(sys.argv[2])); c.set_timeout(10.0)\nprint(c.get_server_version())"
    try:
        done = subprocess.run([python, "-c", code, host, str(port)], capture_output=True, text=True, timeout=30, env=runner_env(carla_root))
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION_IN_FOLDER.search(done.stdout or "")
    return match.group(1) if done.returncode == 0 and match else None


# ---------------------------------------------------------------- setup steps


def _download_scenario_runner(spec: RunnerSpec, target: Path, log: Log) -> None:
    if (target / "scenario_runner.py").is_file():
        return
    url = SCENARIO_RUNNER_ZIP.format(tag=spec.tag)
    log(f"Tải ScenarioRunner {spec.tag}…")
    try:
        response = httpx.get(url, follow_redirects=True, timeout=300)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SetupFailed(f"Không tải được {url}: {exc}") from exc
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(staging)
    inner = next(staging.iterdir())  # scenario_runner-<version>/
    shutil.rmtree(target, ignore_errors=True)
    inner.rename(target)
    shutil.rmtree(staging, ignore_errors=True)


def apply_patches(scenario_runner: Path, names: tuple[str, ...]) -> list[str]:
    """Applies each patch once; returns a line per patch ("đã vá", "đã có", "không cần")."""
    path = scenario_runner / PATCHED_FILE
    text = path.read_text(encoding="utf-8")
    report = []
    for name in names:
        original, fixed = PATCHES[name]
        if fixed in text:
            report.append(f"{name}: đã có")
        elif text.count(original) == 1:
            text = text.replace(original, fixed)
            report.append(f"{name}: đã vá")
        else:
            report.append(f"{name}: không thấy đoạn cần vá (bản này có thể không có lỗi)")
    path.write_text(text, encoding="utf-8")
    return report


def _client_wheel(carla_root: Path | None, version: str, python: str) -> str:
    """The CARLA install's own client wheel for this Python (always matches the server), else the PyPI release."""
    if carla_root:
        tag = "cp" + python.replace(".", "")
        for wheel in sorted((carla_root / "PythonAPI" / "carla" / "dist").glob(f"carla-{version}-{tag}-*.whl")):
            return str(wheel)
    return f"carla=={version}"


def install(version: str, carla_root: Path | None, log: Log) -> BridgeConfig:
    """Creates (or completes) the runner for `version` and points the Bridge config at it."""
    spec = RUNNERS.get(version)
    if spec is None:
        raise SetupFailed(f"CARLA {version} chưa được hỗ trợ (hỗ trợ: {', '.join(RUNNERS)}).")
    uv = find_uv()
    if uv is None:
        raise SetupFailed("Không thấy uv. Cài bằng lệnh trên trang Start up (hoặc https://docs.astral.sh/uv/) rồi chạy lại.")
    base = runtime_dir(version)
    base.mkdir(parents=True, exist_ok=True)
    venv, scenario_runner = base / "venv", base / "scenario_runner"
    python = venv_python(venv)

    if not python.is_file():
        log(f"Tạo môi trường Python {spec.python} (uv tự tải Python nếu máy chưa có)…")
        _run([uv, "venv", "--python", spec.python, str(venv)], "Tạo môi trường Python")
    _download_scenario_runner(spec, scenario_runner, log)
    for line in apply_patches(scenario_runner, spec.patches):
        log(f"  {line}")

    wheel = _client_wheel(carla_root, version, spec.python)
    marker = base / ".installed"
    wanted = f"{spec.tag}|{wheel}"
    if not marker.is_file() or marker.read_text(encoding="utf-8") != wanted:
        log(f"Cài thư viện CARLA {version} và ScenarioRunner (lần đầu có thể mất vài phút)…")
        _run([uv, "pip", "install", "--python", str(python), wheel, "-r", str(scenario_runner / "requirements.txt")], "Cài thư viện")
        marker.write_text(wanted, encoding="utf-8")

    log("Kiểm tra môi trường…")
    root = str(carla_root) if carla_root else None
    check = "import carla, py_trees\n" + ("import agents.navigation.global_route_planner\n" if root else "") + "print('ok')"
    _run([str(python), "-c", check], "Kiểm tra môi trường", env=runner_env(root, str(scenario_runner)))

    cfg = config_store.load()
    cfg.runner_python, cfg.runner_root, cfg.carla_version = str(python), str(scenario_runner), version
    cfg.carla_root = root or ""
    config_store.save(cfg)
    return cfg


# ---------------------------------------------------------------- catalog sync through the runner


def _catalog_script() -> Path:
    """carla_catalog.py as a file the runner Python can execute (bundled as data in the PyInstaller build)."""
    source = Path(carla_catalog.__file__)
    if source.suffix == ".py" and source.is_file():
        return source
    return Path(getattr(sys, "_MEIPASS", "")) / "scenario_forge_bridge" / "carla_catalog.py"


def collect(
    cfg: BridgeConfig,
    *,
    maps: list[str] | None = None,
    load_opt: bool = False,
    load_timeout: float = carla_catalog.LOAD_TIMEOUT_S,
    on_loading: Callable[[int, int, str], None] | None = None,
) -> Iterator[MapResult]:
    """carla_catalog.collect() run in the runner Python; falls back to this process when no runner is set up
    (older setups that installed `carla` next to the Bridge)."""
    if not cfg.runner_python or not Path(cfg.runner_python).is_file():
        yield from carla_catalog.collect(cfg.carla_host, cfg.carla_port, maps=maps, load_opt=load_opt,
                                         load_timeout=load_timeout, on_loading=on_loading)
        return
    command = [cfg.runner_python, str(_catalog_script()), "--host", cfg.carla_host, "--port", str(cfg.carla_port),
               "--load-timeout", str(load_timeout)]
    if maps:
        command += ["--maps", ",".join(maps)]
    if load_opt:
        command.append("--load-opt")
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
                                   env=runner_env(cfg.carla_root or None))
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.pop("event", None)
                if kind == "loading" and on_loading:
                    on_loading(event["index"], event["total"], event["map_name"])
                elif kind == "result":
                    yield MapResult(**event)
                elif kind == "fatal":
                    raise CarlaUnavailable(event.get("error") or "Lỗi khi đọc CARLA")
            if process.wait() != 0:
                errors.seek(0)
                raise CarlaUnavailable(f"Không đọc được dữ liệu CARLA (mã {process.returncode}):\n{errors.read()[-1500:]}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
