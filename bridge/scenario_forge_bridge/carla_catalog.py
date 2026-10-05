"""Reads the user's CARLA through the `carla` Python API and builds one catalog.v1 document per map.

Same shape as backend/app/modules/catalog/contract.py (CatalogV1). Blueprints and weather presets are global;
spawn points, lane waypoints and the OpenDRIVE hash need the map loaded, so every map is loaded in turn and the
map the user had open is restored at the end.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

CATALOG_FORMAT = "scenario-forge.catalog.v1"
WAYPOINT_SPACING_M = 2.0
RPC_TIMEOUT_S = 30.0
# Large maps (Town12/13, Town15) can take minutes to load.
LOAD_TIMEOUT_S = 300.0


class CarlaUnavailable(Exception):
    """The `carla` package is missing or the simulator cannot be reached."""


@dataclass(frozen=True)
class MapResult:
    index: int
    total: int
    map_name: str
    catalog: dict[str, Any] | None = None
    error: str | None = None


def import_carla():
    try:
        import carla  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CarlaUnavailable(
            "Chưa cài gói Python `carla`. Cài đúng phiên bản với CARLA server, ví dụ:\n"
            "  pipx inject scenario-forge-bridge carla==0.9.16\n"
            "(cài bằng pip thường thì: pip install carla==0.9.16)"
        ) from exc
    return carla


def short_map_name(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _r(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def _pose(transform: Any) -> dict[str, float]:
    location, rotation = transform.location, transform.rotation
    return {"x": _r(location.x), "y": _r(location.y), "z": _r(location.z), "yaw": _r(rotation.yaw)}


def _waypoint(waypoint: Any) -> dict[str, Any]:
    return {
        **_pose(waypoint.transform),
        "road_id": int(waypoint.road_id),
        "section_id": int(waypoint.section_id),
        "lane_id": int(waypoint.lane_id),
        "s": _r(waypoint.s),
        "lane_width": _r(waypoint.lane_width) or 3.5,
        "is_junction": bool(waypoint.is_junction),
        "lane_type": str(waypoint.lane_type).rsplit(".", 1)[-1],
    }


def _attribute(blueprint: Any, key: str) -> str | None:
    if not blueprint.has_attribute(key):
        return None
    value = blueprint.get_attribute(key)
    recommended = list(getattr(value, "recommended_values", []) or [])
    return str(recommended[0]) if recommended else str(value.as_str() if hasattr(value, "as_str") else value)


def blueprints(world: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vehicles, walkers = [], []
    for blueprint in world.get_blueprint_library():
        if blueprint.id.startswith("vehicle."):
            wheels = _attribute(blueprint, "number_of_wheels")
            base_type = (_attribute(blueprint, "base_type") or "").lower() or None
            vehicles.append({"id": blueprint.id, "base_type": base_type, "number_of_wheels": int(wheels) if wheels and wheels.isdigit() else None})
        elif blueprint.id.startswith("walker.pedestrian"):
            walkers.append({"id": blueprint.id})
    return sorted(vehicles, key=lambda item: item["id"]), sorted(walkers, key=lambda item: item["id"])


def weather_presets(carla: Any) -> list[str]:
    names = []
    for name in dir(carla.WeatherParameters):
        if name.startswith("_"):
            continue
        try:
            value = getattr(carla.WeatherParameters, name)
        except Exception:  # noqa: BLE001 - some attributes are descriptors
            continue
        if isinstance(value, carla.WeatherParameters):
            names.append(name)
    return sorted(names)


def map_catalog(
    world: Any, *, carla_version: str, available_maps: list[str], vehicles: list, walkers: list, presets: list[str], spacing: float
) -> dict[str, Any]:
    carla_map = world.get_map()
    opendrive = carla_map.to_opendrive()
    return {
        "format": CATALOG_FORMAT,
        "carla_version": carla_version,
        "map_name": short_map_name(carla_map.name),
        "available_maps": available_maps,
        "opendrive_hash": hashlib.sha256(opendrive.encode("utf-8")).hexdigest() if opendrive else None,
        "vehicles": vehicles,
        "walkers": walkers,
        "spawn_points": [_pose(item) for item in carla_map.get_spawn_points()],
        "waypoints": [_waypoint(item) for item in carla_map.generate_waypoints(spacing)],
        "weather_presets": presets,
    }


def collect(
    host: str,
    port: int,
    *,
    maps: list[str] | None = None,
    spacing: float = WAYPOINT_SPACING_M,
    on_loading: Callable[[int, int, str], None] | None = None,
) -> Iterator[MapResult]:
    """Yields one MapResult per map (catalog or error). A failing map does not stop the others."""
    carla = import_carla()
    try:
        client = carla.Client(host, port)
        client.set_timeout(RPC_TIMEOUT_S)
        carla_version = client.get_server_version()
        world = client.get_world()
    except RuntimeError as exc:
        raise CarlaUnavailable(f"Không kết nối được CARLA ở {host}:{port}: {exc}") from exc

    original = short_map_name(world.get_map().name)
    available = sorted(short_map_name(item) for item in client.get_available_maps())
    wanted = {short_map_name(item) for item in maps} if maps else None
    targets = [name for name in available if wanted is None or name in wanted]
    # Blueprint library and weather presets do not depend on the map.
    vehicles, walkers = blueprints(world)
    presets = weather_presets(carla)
    current = original
    try:
        for index, name in enumerate(targets, start=1):
            if on_loading:
                on_loading(index, len(targets), name)
            try:
                if name != current:
                    client.set_timeout(LOAD_TIMEOUT_S)
                    world = client.load_world(name)
                    client.set_timeout(RPC_TIMEOUT_S)
                    current = name
                catalog = map_catalog(world, carla_version=carla_version, available_maps=available, vehicles=vehicles,
                                      walkers=walkers, presets=presets, spacing=spacing)
                if not catalog["spawn_points"] or not catalog["waypoints"]:
                    yield MapResult(index, len(targets), name, error="Map không có spawn point hoặc waypoint")
                    continue
                yield MapResult(index, len(targets), name, catalog=catalog)
            except Exception as exc:  # noqa: BLE001 - report per map, keep going
                client.set_timeout(RPC_TIMEOUT_S)
                yield MapResult(index, len(targets), name, error=str(exc) or exc.__class__.__name__)
    finally:
        if current != original:
            try:
                client.set_timeout(LOAD_TIMEOUT_S)
                client.load_world(original)
            except Exception:  # noqa: BLE001 - best effort: the user can reopen their map
                pass
