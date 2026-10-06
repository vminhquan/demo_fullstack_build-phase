from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from scenario_forge_bridge import runtime
from scenario_forge_bridge.carla_catalog import CarlaUnavailable
from scenario_forge_bridge.config import BridgeConfig

FAKE_CARLA = Path(__file__).parent / "fake_carla" / "carla"

# The two spots of ScenarioRunner v0.9.16 atomic_behaviors.py the patches target.
ATOMIC_BEHAVIORS = (
    "class ChangeActorControl:\n"
    "    def update(self):\n"
    "        if actor_dict:\n"
    "            if self._actor.id in actor_dict:\n"
    "                actor_dict[self._actor.id].reset()\n"
    "\n"
    "class ChangeActorWaypoints:\n"
    "    def __init__(self):\n"
    "        if len(self._waypoints) != len(self._times):\n"
    "            raise ValueError\n"
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_BRIDGE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CARLA_ROOT", raising=False)


def carla_install(base: Path, version: str = "0.9.16", wheels: tuple[str, ...] = ("cp310", "cp311", "cp312")) -> Path:
    """A CARLA folder as the release ships it: PythonAPI/carla/{agents,dist}, here with the test's fake `carla`."""
    root = base / f"CARLA_{version}"
    api = root / "PythonAPI" / "carla"
    (api / "agents" / "navigation").mkdir(parents=True)
    (api / "dist").mkdir()
    for tag in wheels:
        (api / "dist" / f"carla-{version}-{tag}-{tag}-manylinux_2_31_x86_64.whl").write_bytes(b"")
    shutil.copytree(FAKE_CARLA, api / "carla", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def test_version_comes_from_the_bundled_wheels(tmp_path) -> None:
    root = carla_install(tmp_path)
    renamed = root.rename(tmp_path / "simulator")  # the folder name no longer says it
    assert runtime.is_carla_root(renamed)
    assert runtime.carla_version_of(renamed) == "0.9.16"
    assert runtime.carla_version_of(tmp_path) is None


def test_find_carla_roots_uses_the_explicit_folder_and_skips_non_carla(tmp_path) -> None:
    root = carla_install(tmp_path)
    (tmp_path / "CARLA_notes").mkdir()
    assert runtime.find_carla_roots(str(root))[0] == root.resolve()
    assert tmp_path / "CARLA_notes" not in runtime.find_carla_roots(str(tmp_path / "CARLA_notes"))


def test_client_wheel_matches_the_runner_python(tmp_path) -> None:
    root = carla_install(tmp_path)
    assert runtime._client_wheel(root, "0.9.16", "3.10").endswith("carla-0.9.16-cp310-cp310-manylinux_2_31_x86_64.whl")
    assert runtime._client_wheel(None, "0.9.16", "3.10") == "carla==0.9.16"
    assert runtime._client_wheel(carla_install(tmp_path / "other", "0.9.15", ("cp37",)), "0.9.15", "3.8") == "carla==0.9.15"


def test_patches_apply_once_and_report_missing_spots(tmp_path) -> None:
    target = tmp_path / runtime.PATCHED_FILE
    target.parent.mkdir(parents=True)
    target.write_text(ATOMIC_BEHAVIORS)
    assert runtime.apply_patches(tmp_path, ("times_none", "self_reset")) == ["times_none: đã vá", "self_reset: đã vá"]
    patched = target.read_text()
    assert "if self._times is not None and len(self._waypoints)" in patched
    assert "if actor_dict[self._actor.id] is not self._actor_control:" in patched
    assert runtime.apply_patches(tmp_path, ("times_none", "self_reset")) == ["times_none: đã có", "self_reset: đã có"]
    assert target.read_text() == patched
    target.write_text("nothing to patch\n")
    assert runtime.apply_patches(tmp_path, ("times_none",))[0].startswith("times_none: không thấy")


def test_runner_env_drops_foreign_python_settings(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "/usr/lib/python3.14")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/site-packages")
    monkeypatch.setenv("VIRTUAL_ENV", "/home/u/carla-env")
    env = runtime.runner_env("/opt/CARLA", "/opt/sr")
    assert "PYTHONHOME" not in env and "VIRTUAL_ENV" not in env
    assert env["PYTHONPATH"] == str(Path("/opt/CARLA", "PythonAPI", "carla"))
    assert env["SCENARIO_RUNNER_ROOT"] == "/opt/sr"


def test_install_needs_a_supported_version_and_uv(monkeypatch) -> None:
    with pytest.raises(runtime.SetupFailed, match="chưa được hỗ trợ"):
        runtime.install("0.9.14", None, lambda _: None)
    monkeypatch.setattr(runtime, "find_uv", lambda: None)
    with pytest.raises(runtime.SetupFailed, match="uv"):
        runtime.install("0.9.16", None, lambda _: None)


def test_catalog_sync_runs_in_the_runner_python(tmp_path) -> None:
    # The fake `carla` lives only in the CARLA folder: the result proves the read happened in the runner process.
    root = carla_install(tmp_path)
    cfg = BridgeConfig(runner_python=sys.executable, carla_root=str(root))
    loading: list[tuple[int, int, str]] = []
    results = list(runtime.collect(cfg, maps=["Town03"], on_loading=lambda i, n, name: loading.append((i, n, name))))
    assert [item.map_name for item in results] == ["Town03"]
    assert results[0].catalog["map_name"] == "Town03" and results[0].catalog["waypoints"]
    assert loading and loading[0][2].startswith("Town03")


def test_catalog_sync_reports_an_unreachable_carla(tmp_path) -> None:
    root = carla_install(tmp_path)
    (root / "PythonAPI" / "carla" / "carla" / "__init__.py").write_text("raise ImportError('no carla here')\n")
    cfg = BridgeConfig(runner_python=sys.executable, carla_root=str(root))
    with pytest.raises(CarlaUnavailable, match="setup-runner"):
        list(runtime.collect(cfg))
