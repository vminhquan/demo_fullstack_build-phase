from __future__ import annotations

import socket

import pytest

from scenario_forge_bridge import config as config_store
from scenario_forge_bridge import credentials
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.config import BridgeConfig, Connection


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SF_BRIDGE_TOKEN_STORE", "file")


def test_probe_detects_open_and_closed_port() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert probe_carla("127.0.0.1", port).reachable
    closed = probe_carla("127.0.0.1", port, timeout=0.5)
    assert not closed.reachable and closed.error
    assert "error" not in closed.payload()


def test_ws_url_from_server() -> None:
    assert BridgeConfig(server="http://localhost:8000/api/v1/").ws_url == "ws://localhost:8000/api/v1/bridge/ws"
    assert BridgeConfig(server="https://forge.example.com/api/v1").ws_url == "wss://forge.example.com/api/v1/bridge/ws"


def test_config_round_trip_and_one_connection_per_project() -> None:
    cfg = BridgeConfig(server="http://x/api/v1", bridge_uid="BRG-1")
    cfg.upsert_connection(Connection("CONN-A", 5, "P5", "2026-10-04T00:00:00Z"))
    cfg.upsert_connection(Connection("CONN-B", 5, "P5", "2026-10-05T00:00:00Z"))
    cfg.upsert_connection(Connection("CONN-C", 6, "P6", "2026-10-05T00:00:00Z"))
    config_store.save(cfg)
    loaded = config_store.load()
    assert loaded.bridge_uid == "BRG-1"
    assert [item.connection_uid for item in loaded.connections] == ["CONN-B", "CONN-C"]
    loaded.remove_connection("CONN-B")
    assert [item.project_id for item in loaded.connections] == [6]


def test_token_file_store() -> None:
    assert credentials.load_token() is None
    where = credentials.save_token("secret-token")
    assert where.endswith("device-token")
    assert credentials.load_token() == "secret-token"
    credentials.delete_token()
    assert credentials.load_token() is None
