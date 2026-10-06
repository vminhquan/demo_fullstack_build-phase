"""Bridge settings in the per-user config folder (platformdirs):

- Ubuntu:  ~/.config/scenario-forge-bridge/config.json
- Windows: %LOCALAPPDATA%\\ScenarioForge\\scenario-forge-bridge\\config.json
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

from scenario_forge_bridge.carla_probe import DEFAULT_HOST, DEFAULT_PORT

APP_NAME = "scenario-forge-bridge"
APP_AUTHOR = "ScenarioForge"
DEFAULT_SERVER = "http://localhost:8000/api/v1"


def config_dir() -> Path:
    override = os.environ.get("SF_BRIDGE_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir(APP_NAME, APP_AUTHOR))


@dataclass
class Connection:
    connection_uid: str
    project_id: int
    project_name: str
    paired_at: str


@dataclass
class BridgeConfig:
    server: str = DEFAULT_SERVER
    name: str = ""
    bridge_uid: str | None = None
    carla_host: str = DEFAULT_HOST
    carla_port: int = DEFAULT_PORT
    # Simulator Runner: a separate Python (with carla + ScenarioRunner deps) runs scenario_runner.py.
    # The packaged Bridge has no `carla` package, so everything touching CARLA runs in that Python.
    runner_python: str = ""
    runner_root: str = ""   # folder holding scenario_runner.py
    carla_root: str = ""    # CARLA install; PythonAPI/carla provides the `agents` package
    carla_version: str = ""  # CARLA server version the runner was set up for (`setup-runner`)
    camera: str = "follow"  # follow: move the CARLA window camera onto the ego during a run; off: leave it
    connections: list[Connection] = field(default_factory=list)

    @property
    def ws_url(self) -> str:
        base = self.server.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return f"{base}/bridge/ws"

    def upsert_connection(self, connection: Connection) -> None:
        self.connections = [item for item in self.connections if item.project_id != connection.project_id] + [connection]

    def remove_connection(self, connection_uid: str) -> None:
        self.connections = [item for item in self.connections if item.connection_uid != connection_uid]


def path() -> Path:
    return config_dir() / "config.json"


def load() -> BridgeConfig:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BridgeConfig()
    connections = [Connection(**item) for item in data.pop("connections", []) if isinstance(item, dict)]
    known = {key: value for key, value in data.items() if key in BridgeConfig.__dataclass_fields__}
    return BridgeConfig(**known, connections=connections)


def save(config: BridgeConfig) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
