"""Reads the user's CARLA through the `carla` Python API and builds one catalog.v1 document per map.

Same shape as backend/app/modules/catalog/contract.py (CatalogV1). Blueprints and weather presets are global;
spawn points, lane waypoints and the OpenDRIVE hash need the map loaded, so every map is loaded in turn and the
map the user had open is restored at the end.
"""
from __future__ import annotations

import hashlib
import re
import time
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


_ATTRIBUTE_VALUE = re.compile(r"value=([^()]*)\(")


def _attribute(blueprint: Any, key: str) -> str | None:
    """Attribute value as text. CARLA attributes are typed (number_of_wheels is Int): `as_str()` on a non-string
    attribute raises "bad attribute cast", so read it with the getter of its own type."""
    if not blueprint.has_attribute(key):
        return None
    value = blueprint.get_attribute(key)
    recommended = list(getattr(value, "recommended_values", []) or [])
    if recommended:
        return str(recommended[0])
    kind = str(getattr(value, "type", "")).rsplit(".", 1)[-1]
    getters = {"Int": "as_int", "Float": "as_float", "Bool": "as_bool"}
    for getter in (getters.get(kind), "as_str", "as_int"):
        if getter and hasattr(value, getter):
            try:
                return str(getattr(value, getter)())
            except RuntimeError:
                continue
    # Last resort: "ActorAttribute(id=number_of_wheels, type=int, value=4(const))"
    match = _ATTRIBUTE_VALUE.search(str(value))
    return match.group(1) if match else None


def blueprints(world: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vehicles, walkers = [], []
    for blueprint in world.get_blueprint_library():
        if blueprint.id.startswith("vehicle."):
            try:
                wheels = _attribute(blueprint, "number_of_wheels")
                base_type = (_attribute(blueprint, "base_type") or "").lower() or None
            except Exception:  # noqa: BLE001 - one odd blueprint must not stop the sync; the backend infers the type
                wheels, base_type = None, None
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


# Utility levels CARLA ships next to the towns: no OpenDRIVE road network, loading them fails ("failed to generate map").
NON_ROAD_PREFIXES = ("Annotation",)
OPT_SUFFIX = "_Opt"
ALIVE_ATTEMPTS = 6
ALIVE_WAIT_S = 10.0  # x ALIVE_ATTEMPTS = up to a minute of patience after a failed load


def is_road_map(name: str) -> bool:
    return not name.startswith(NON_ROAD_PREFIXES)


def road_network(name: str) -> str:
    """Town01 and Town01_Opt are the same town (the _Opt build only splits the art into layers)."""
    return name[: -len(OPT_SUFFIX)] if name.endswith(OPT_SUFFIX) else name


def plan_loads(targets: list[str], current: str, *, load_opt: bool) -> list[list[str]]:
    """Groups map names that share one road network, so each network is loaded once.

    Each group is [map to load, *other names that reuse its data]. The open map is read first (no load);
    otherwise the plain build is preferred over _Opt (lighter, and the one that loaded reliably on 0.9.16).
    """
    if load_opt:
        groups = [[name] for name in targets]
    else:
        by_network: dict[str, list[str]] = {}
        for name in targets:
            by_network.setdefault(road_network(name), []).append(name)
        groups = []
        for names in by_network.values():
            names.sort(key=lambda item: (item != current, item.endswith(OPT_SUFFIX), item))
            groups.append(names)
    return sorted(groups, key=lambda group: (current not in group, group[0]))


def _server_alive(client: Any) -> bool:
    """After a failed load the server may still be busy (or have crashed): wait a bit before giving up."""
    client.set_timeout(ALIVE_WAIT_S)
    for _ in range(ALIVE_ATTEMPTS):
        try:
            client.get_server_version()
            return True
        except RuntimeError:
            time.sleep(ALIVE_WAIT_S)
    return False


def collect(
    host: str,
    port: int,
    *,
    maps: list[str] | None = None,
    spacing: float = WAYPOINT_SPACING_M,
    load_opt: bool = False,
    load_timeout: float = LOAD_TIMEOUT_S,
    on_loading: Callable[[int, int, str], None] | None = None,
) -> Iterator[MapResult]:
    """Yields one MapResult per map name (catalog, or error / skip reason). A failing map does not stop the others,
    but a simulator that stops answering ends the run instead of waiting `load_timeout` for every remaining map."""
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
    # Utility levels are skipped unless asked for by name.
    targets = [name for name in available if (name in wanted if wanted is not None else is_road_map(name))]
    groups = plan_loads(targets, original, load_opt=load_opt)
    total = len(targets)
    # Blueprint library and weather presets do not depend on the map.
    vehicles, walkers = blueprints(world)
    presets = weather_presets(carla)
    current = original
    index = 0
    alive = True
    try:
        for position, group in enumerate(groups):
            name, twins = group[0], group[1:]
            if on_loading:
                on_loading(index + 1, total, name + (f" (dùng chung cho {', '.join(twins)})" if twins else ""))
            try:
                if name != current:
                    client.set_timeout(load_timeout)
                    world = client.load_world(name)
                    current = name
                client.set_timeout(RPC_TIMEOUT_S)
                catalog = map_catalog(world, carla_version=carla_version, available_maps=available, vehicles=vehicles,
                                      walkers=walkers, presets=presets, spacing=spacing)
            except Exception as exc:  # noqa: BLE001 - report per map, keep going
                message = str(exc) or exc.__class__.__name__
                if "failed to generate map" in message:
                    message = "Map không có mạng đường OpenDRIVE (map tiện ích), bỏ qua"
                for member in group:
                    index += 1
                    yield MapResult(index, total, member, error=message)
                if not _server_alive(client):
                    alive = False
                    for rest in groups[position + 1:]:
                        for member in rest:
                            index += 1
                            yield MapResult(index, total, member, error="Bỏ qua: CARLA không phản hồi")
                    raise CarlaUnavailable(
                        f"CARLA không phản hồi sau khi mở {name} (có thể đã treo hoặc crash). "
                        "Khởi động lại CARLA rồi chạy lại; các map đã gửi vẫn được giữ, có thể dùng --maps để chỉ đồng bộ map còn thiếu."
                    ) from exc
                # The server answers again: re-read which map it actually has open.
                client.set_timeout(RPC_TIMEOUT_S)
                world = client.get_world()
                current = short_map_name(world.get_map().name)
                continue
            if not catalog["spawn_points"] or not catalog["waypoints"]:
                for member in group:
                    index += 1
                    yield MapResult(index, total, member, error="Map không có spawn point hoặc waypoint")
                continue
            for member in group:
                index += 1
                # [Inference] _Opt and plain builds share the OpenDRIVE: reuse the roads, keep the requested name.
                yield MapResult(index, total, member, catalog={**catalog, "map_name": member})
    finally:
        # A dead server would block load_world for the whole timeout.
        if alive and current != original:
            try:
                client.set_timeout(load_timeout)
                client.load_world(original)
            except Exception:  # noqa: BLE001 - best effort: the user can reopen their map
                pass
