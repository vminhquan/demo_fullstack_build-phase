"""CARLA reachability check: a TCP connect to the simulator's RPC port (2000 by default). No `carla` package needed."""
from __future__ import annotations

import socket
from dataclasses import asdict, dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2000


@dataclass(frozen=True)
class CarlaProbe:
    host: str
    port: int
    reachable: bool
    error: str | None = None

    def payload(self) -> dict:
        """Shape sent to the backend (the error text stays local)."""
        data = asdict(self)
        data.pop("error")
        return data


def probe_carla(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 1.5) -> CarlaProbe:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CarlaProbe(host, port, True)
    except OSError as exc:
        return CarlaProbe(host, port, False, str(exc) or exc.__class__.__name__)
