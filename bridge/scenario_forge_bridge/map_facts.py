"""Facts of a loaded CARLA map for catalog.v2: lane rules, road speeds, junctions, landmarks, traffic lights,
crosswalks and topology; plus vehicle sizes and weather values, which do not depend on the map.

Runs next to carla_catalog.py in the Simulator Runner's Python (3.8+): standard library only. Each reader is
independent and wrapped by `guarded`: an API missing on the user's CARLA version is reported in
`extraction_errors` instead of failing the whole map.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

# OpenDRIVE <speed unit>: m/s when absent.
SPEED_TO_KMH = {"m/s": 3.6, "km/h": 1.0, "mph": 1.609344}
MAX_LANDMARKS = 2000
SPAWN_TRIES = 5
WEATHER_FIELDS = (
    "cloudiness", "precipitation", "precipitation_deposits", "wind_intensity", "sun_azimuth_angle",
    "sun_altitude_angle", "fog_density", "fog_distance", "fog_falloff", "wetness", "scattering_intensity",
    "mie_scattering_scale", "rayleigh_scattering_scale", "dust_storm",
)


def guarded(errors: List[Dict[str, str]], part: str, read: Callable[[], Any], default: Any = None) -> Any:
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - one unsupported API must not fail the map
        errors.append({"part": part, "error": (str(exc) or exc.__class__.__name__)[:300]})
        return default


def _try(read: Callable[[], Any], default: Any = None) -> Any:
    try:
        return read()
    except Exception:  # noqa: BLE001 - optional detail of one item
        return default


def enum_name(value: Any) -> str:
    return str(value).rsplit(".", 1)[-1]


def norm_yaw(yaw: float) -> float:
    """Degrees in [-180, 180): CARLA returns values such as -539.8 or 450.7."""
    return round((float(yaw) + 180.0) % 360.0 - 180.0, 2)


def lane_ref(waypoint: Any) -> Dict[str, int]:
    return {"road_id": int(waypoint.road_id), "section_id": int(waypoint.section_id), "lane_id": int(waypoint.lane_id)}


def point(location: Any) -> Dict[str, float]:
    return {"x": round(float(location.x), 2), "y": round(float(location.y), 2), "z": round(float(location.z), 2)}


def waypoint_point(waypoint: Any) -> Dict[str, Any]:
    transform = waypoint.transform
    return {**lane_ref(waypoint), "s": round(float(waypoint.s), 2), **point(transform.location),
            "yaw": norm_yaw(transform.rotation.yaw)}


# ---------------------------------------------------------------- lane rules (per waypoint)

def _marking(marking: Any) -> Optional[Dict[str, str]]:
    if marking is None:
        return None
    return {"type": enum_name(marking.type), "color": enum_name(marking.color), "lane_change": enum_name(marking.lane_change)}


def _neighbor(waypoint: Any, side: str) -> Optional[Dict[str, Any]]:
    try:
        other = getattr(waypoint, "get_%s_lane" % side)()
    except RuntimeError:
        return None
    if other is None:
        return None
    return {**lane_ref(other), "s": round(float(other.s), 2), "lane_type": enum_name(other.lane_type)}


def lane_rules(waypoint: Any) -> Dict[str, Any]:
    """lane_change is what the waypoint's own lane allows; a neighbor with the other lane_id sign runs the other way."""
    return {
        "junction_id": int(waypoint.junction_id) if waypoint.is_junction else None,
        "lane_change": enum_name(waypoint.lane_change),
        "left_marking": _marking(waypoint.left_lane_marking),
        "right_marking": _marking(waypoint.right_lane_marking),
        "left_lane": _neighbor(waypoint, "left"),
        "right_lane": _neighbor(waypoint, "right"),
    }


# ---------------------------------------------------------------- OpenDRIVE (speeds, junction connections)

def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def road_speeds(xodr: str) -> List[Dict[str, Any]]:
    """<road><type s><speed max unit/></type></road> as km/h numbers; "no limit" / "undefined" are skipped."""
    speeds = []
    for road in ET.fromstring(xodr).iter("road"):
        road_id = _int(road.get("id"))
        for road_type in road.findall("type"):
            speed = road_type.find("speed")
            if road_id is None or speed is None:
                continue
            factor = SPEED_TO_KMH.get((speed.get("unit") or "m/s").strip().lower())
            try:
                value = float(speed.get("max") or "")
            except ValueError:
                continue
            if factor is None or not math.isfinite(value) or value <= 0:
                continue
            speeds.append({"road_id": road_id, "from_s": round(float(road_type.get("s") or 0.0), 2),
                           "max_kmh": round(value * factor, 1)})
    return sorted(speeds, key=lambda item: (item["road_id"], item["from_s"]))


def speed_at(speeds: List[Dict[str, Any]], road_id: int, s: float) -> Optional[float]:
    found = None
    for item in speeds:
        if item["road_id"] == road_id and item["from_s"] <= s + 1e-6:
            found = item["max_kmh"]
    return found


def xodr_junctions(xodr: str) -> Dict[int, Dict[str, Any]]:
    junctions: Dict[int, Dict[str, Any]] = {}
    for junction in ET.fromstring(xodr).iter("junction"):
        junction_id = _int(junction.get("id"))
        if junction_id is None:
            continue
        connections = []
        for connection in junction.findall("connection"):
            incoming, connecting = _int(connection.get("incomingRoad")), _int(connection.get("connectingRoad"))
            if incoming is None or connecting is None:
                continue
            links = [{"from_lane": _int(link.get("from")), "to_lane": _int(link.get("to"))} for link in connection.findall("laneLink")]
            connections.append({"incoming_road_id": incoming, "connecting_road_id": connecting,
                                "contact_point": connection.get("contactPoint"),
                                "lane_links": [link for link in links if link["from_lane"] is not None and link["to_lane"] is not None]})
        junctions[junction_id] = {"connections": connections}
    return junctions


# ---------------------------------------------------------------- map API readers

def junctions(carla: Any, waypoints: List[Any], xodr: Optional[str]) -> List[Dict[str, Any]]:
    from_xodr = xodr_junctions(xodr) if xodr else {}
    found: Dict[int, Dict[str, Any]] = {}
    for waypoint in waypoints:
        if not waypoint.is_junction or int(waypoint.junction_id) in found:
            continue
        junction = waypoint.get_junction()
        box = junction.bounding_box
        paths = []
        for entry, exit_ in junction.get_waypoints(carla.LaneType.Driving):
            paths.append({"entry": waypoint_point(entry), "exit": waypoint_point(exit_)})
        found[int(junction.id)] = {"center": point(box.location), "extent": point(box.extent), "lane_paths": paths}
    result = []
    for junction_id in sorted(set(found) | set(from_xodr)):
        connections = from_xodr.get(junction_id, {}).get("connections", [])
        incoming = sorted({item["incoming_road_id"] for item in connections})
        result.append({
            "junction_id": junction_id,
            **found.get(junction_id, {"center": None, "extent": None, "lane_paths": []}),
            "incoming_road_ids": incoming,
            "incoming_road_count": len(incoming),
            # Four incoming roads is a hint, not proof of a four-way crossing.
            "four_way_candidate": len(incoming) >= 4,
            "connections": connections,
        })
    return result


def landmarks(carla_map: Any) -> List[Dict[str, Any]]:
    """Signals and signs on the roads (type 1000001 = traffic light, 206 = stop, 205 = yield, 274 = speed limit)."""
    return [_landmark(item) for item in list(carla_map.get_all_landmarks())[:MAX_LANDMARKS]]


def _landmark(landmark: Any) -> Dict[str, Any]:
    transform = _try(lambda: landmark.transform)
    return {
        "id": str(landmark.id), "type": str(landmark.type), "sub_type": _try(lambda: str(landmark.sub_type)),
        "name": _try(lambda: str(landmark.name)), "road_id": int(landmark.road_id), "s": round(float(landmark.s), 2),
        "value": _try(lambda: float(landmark.value)), "unit": _try(lambda: str(landmark.unit)),
        "text": _try(lambda: str(landmark.text)),
        **(dict(point(transform.location), yaw=norm_yaw(transform.rotation.yaw)) if transform is not None else {}),
        "affected_lanes": [[int(first), int(last)] for first, last in _try(landmark.get_lane_validities, [])],
    }


def traffic_lights(world: Any) -> List[Dict[str, Any]]:
    """Static facts of the traffic light actors; the light state at sync time is runtime data and is not kept."""
    lights = [_traffic_light(item) for item in world.get_actors().filter("traffic.traffic_light")]
    return sorted(lights, key=lambda item: item["actor_id"])


def _traffic_light(light: Any) -> Dict[str, Any]:
    affected = {tuple(lane_ref(item).values()): lane_ref(item) for item in _try(light.get_affected_lane_waypoints, [])}
    return {
        "actor_id": int(light.id),
        "opendrive_id": _try(lambda: str(light.get_opendrive_id())),
        **point(light.get_transform().location),
        "pole_index": _try(lambda: int(light.get_pole_index())),
        "group_actor_ids": sorted(int(item.id) for item in _try(light.get_group_traffic_lights, [])),
        "stop_waypoints": [waypoint_point(item) for item in _try(light.get_stop_waypoints, [])],
        "affected_lanes": [affected[key] for key in sorted(affected)],
        "green_s": _try(lambda: round(float(light.get_green_time()), 2)),
        "yellow_s": _try(lambda: round(float(light.get_yellow_time()), 2)),
        "red_s": _try(lambda: round(float(light.get_red_time()), 2)),
    }


def crosswalks(carla: Any, carla_map: Any) -> List[Dict[str, Any]]:
    """get_crosswalks() lists every zone's vertices, each zone closed by repeating its first vertex."""
    polygons, current = [], []
    for location in carla_map.get_crosswalks():
        vertex = point(location)
        if len(current) > 2 and math.dist((vertex["x"], vertex["y"]), (current[0]["x"], current[0]["y"])) < 0.05:
            polygons.append(current)
            current = []
        else:
            current.append(vertex)
    if len(current) > 2:
        polygons.append(current)
    return [_crosswalk(carla, carla_map, polygon) for polygon in polygons]


def _crosswalk(carla: Any, carla_map: Any, polygon: List[Dict[str, float]]) -> Dict[str, Any]:
    center = {axis: round(sum(item[axis] for item in polygon) / len(polygon), 2) for axis in ("x", "y", "z")}
    nearest = _try(lambda: carla_map.get_waypoint(carla.Location(x=center["x"], y=center["y"], z=center["z"]),
                                                  project_to_road=True, lane_type=carla.LaneType.Driving))
    return {"polygon": polygon, "center": center, "nearest_driving_lane": lane_ref(nearest) if nearest is not None else None}


def topology(carla_map: Any) -> List[Dict[str, Any]]:
    edges = []
    for start, end in carla_map.get_topology():
        edges.append({"start": {**lane_ref(start), "s": round(float(start.s), 2)},
                      "end": {**lane_ref(end), "s": round(float(end.s), 2)}})
    return edges


# ---------------------------------------------------------------- map-independent facts

def vehicle_sizes(world: Any) -> Dict[str, Dict[str, float]]:
    """Bounding box of every vehicle blueprint: each is spawned at a free spawn point, read and destroyed at once."""
    spawn_points = list(world.get_map().get_spawn_points())[:SPAWN_TRIES]
    sizes = {}
    for blueprint in world.get_blueprint_library():
        if not blueprint.id.startswith("vehicle."):
            continue
        actor = None
        for transform in spawn_points:
            actor = world.try_spawn_actor(blueprint, transform)
            if actor is not None:
                break
        if actor is None:
            continue
        try:
            extent = actor.bounding_box.extent
            sizes[blueprint.id] = {"length_m": round(2 * float(extent.x), 2), "width_m": round(2 * float(extent.y), 2),
                                   "height_m": round(2 * float(extent.z), 2)}
        finally:
            actor.destroy()
    return sizes


def weather_parameters(carla: Any, names: List[str]) -> Dict[str, Dict[str, float]]:
    values = {}
    for name in names:
        preset = getattr(carla.WeatherParameters, name)
        values[name] = {field: round(float(getattr(preset, field)), 3) for field in WEATHER_FIELDS if hasattr(preset, field)}
    return values
