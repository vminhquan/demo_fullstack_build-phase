"""Read-only answers for agent2's tool calls, computed from the light part of a stored catalog snapshot.

Each tool answers one small question (a summary, a site search, one section of one site, vehicles, weather) so the
LLM only pulls what it needs into its context. Answers are top-N and rounded; unknown values stay null with a
reason instead of being guessed. A `seed` reorders lists so parallel Builder variants are offered different picks.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Literal

MAX_SITES = 20
MAX_VEHICLES = 100
# Objects counted as "on the corridor": ahead of the ego anchor within the verified length, near the road.
WINDOW_MARGIN_M = 10.0
WINDOW_LATERAL_M = 20.0
SPEED_SIGN, STOP_SIGN, YIELD_SIGN = "274", "206", "205"
MOTORCYCLE_ID_HINTS = ("motorcycle", "yamaha", "kawasaki", "vespa", "harley-davidson")

Section = Literal["geometry", "speed", "controls", "junctions"]
# Snapshot JSON keys each section reads, besides cut_in_sites: a section that is not asked for is not loaded.
SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "geometry": (),
    "speed": ("road_speeds", "landmarks"),
    "controls": ("landmarks", "traffic_lights", "crosswalks"),
    "junctions": ("junctions",),
}
GEOMETRY_KEYS = (
    "available_length_m", "upstream_length_m", "lane_width_m", "max_heading_change_deg", "ends_at",
    "marking_between", "lane_change_allowed_throughout", "first_junction_m", "target_side",
)


def _round(value: Any) -> Any:
    return round(value, 1) if isinstance(value, float) else value


def _round_all(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _round(value) for key, value in item.items()}


def _shuffled(items: list[Any], seed: int, key) -> list[Any]:
    """Stable per-seed order: the same seed always gives the same list, another seed another order."""
    return sorted(items, key=lambda item: hashlib.sha256(f"{seed}:{key(item)}".encode()).hexdigest())


def compact_site(site: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: _round(site.get(key)) for key in (
            "site_id", "location_tags", "available_length_m", "speed_limit_kmh", "target_side",
            "lane_change_allowed_throughout", "first_junction_m",
        )
    }
    # Where the ego can start, and on which side of it the verified adjacent lane is (the side a vehicle from that
    # lane comes from: moving left into the ego's lane means it starts on the ego's right).
    ego_lane = site.get("ego_lane") or {}
    compact.update({"ego_road_id": ego_lane.get("road_id"), "ego_lane_id": ego_lane.get("lane_id"), "ego_s": _round(site.get("ego_s")),
                    "adjacent_lane_side": {"left": "right", "right": "left"}.get(site.get("target_side") or "")})
    if compact["speed_limit_kmh"] is None:
        compact["speed_limit_unknown_reason"] = "No OpenDRIVE speed on this road (often a junction connector)."
    return compact


def catalog_summary(document: dict[str, Any]) -> dict[str, Any]:
    """What the whole map offers, so the model can choose filters that match something."""
    sites = document.get("cut_in_sites") or []
    speeds = Counter(site.get("speed_limit_kmh") for site in sites)
    return {
        "map_name": document.get("map_name"),
        "total_sites": len(sites),
        "tags": dict(Counter(tag for site in sites for tag in site.get("location_tags") or [])),
        "speed_limits_kmh": {("unknown" if speed is None else str(_round(speed))): count for speed, count in speeds.items()},
        "max_length_m": _round(max((site.get("available_length_m", 0.0) for site in sites), default=0.0)),
    }


def search_sites(
    document: dict[str, Any],
    *,
    tags: list[str] | None = None,
    min_length_m: float | None = None,
    speed_kmh_min: float | None = None,
    speed_kmh_max: float | None = None,
    target_side: str | None = None,
    lane_change_allowed: bool | None = None,
    near_junction: bool | None = None,
    limit: int = 10,
    seed: int | None = None,
) -> dict[str, Any]:
    sites = document.get("cut_in_sites") or []

    def keep(site: dict[str, Any]) -> bool:
        speed = site.get("speed_limit_kmh")
        return (
            set(tags or []) <= set(site.get("location_tags") or [])
            and (min_length_m is None or site.get("available_length_m", 0) >= min_length_m)
            and (speed_kmh_min is None or (speed is not None and speed >= speed_kmh_min))
            and (speed_kmh_max is None or (speed is not None and speed <= speed_kmh_max))
            and (target_side is None or site.get("target_side") == target_side)
            and (lane_change_allowed is None or site.get("lane_change_allowed_throughout") is lane_change_allowed)
            and (near_junction is None or (site.get("first_junction_m") is not None
                  or bool({"junction", "junction_approach", "intersection_4way"} & set(site.get("location_tags") or []))) is near_junction)
        )

    matching = list(filter(keep, sites))
    if seed is None:
        matching.sort(key=lambda site: (-site.get("available_length_m", 0), site["site_id"]))
    else:
        matching = _shuffled(matching, seed, lambda site: site["site_id"])
    found: dict[str, Any] = {
        "total_matching": len(matching),
        "sites": [compact_site(site) for site in matching[: max(1, min(limit, MAX_SITES))]],
    }
    if not matching:
        # What the map does offer, so the model relaxes the filters instead of giving up.
        found["map_summary"] = catalog_summary(document)
        found["hint"] = "No site matches every filter: drop the filters the user did not ask for and search again."
    return found


def _frame(anchor: dict[str, Any]):
    heading = math.radians(anchor["yaw"])
    cos, sin = math.cos(heading), math.sin(heading)

    def project(point: dict[str, Any]) -> tuple[float, float]:
        dx, dy = point["x"] - anchor["x"], point["y"] - anchor["y"]
        return dx * cos + dy * sin, abs(-dx * sin + dy * cos)

    return project


def _corridor(site: dict[str, Any]):
    """Along/lateral offsets of a point if it lies on the site's corridor, else None."""
    project = _frame(site["ego_anchor"])
    start = -(site.get("upstream_length_m") or 0.0) - WINDOW_MARGIN_M
    end = site["available_length_m"] + WINDOW_MARGIN_M

    def located(point: dict[str, Any] | None, reach: float = 0.0) -> dict[str, float] | None:
        """`reach` widens the window (junction size). Records stored without a position (CARLA left it out) are skipped."""
        if not point or point.get("x") is None or point.get("y") is None:
            return None
        along, lateral = project(point)
        if start - reach <= along <= end + reach and lateral <= WINDOW_LATERAL_M + reach:
            return {"along_m": round(along, 1), "lateral_m": round(lateral, 1)}
        return None

    return located


def _by_along(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: item["along_m"])


def _landmarks(document: dict[str, Any], located, types: tuple[str, ...]) -> list[tuple[dict[str, Any], dict[str, float]]]:
    return [(landmark, where) for landmark in document.get("landmarks") or []
            if landmark.get("type") in types and (where := located(landmark)) is not None]


def _speed(document: dict[str, Any], site: dict[str, Any], located) -> dict[str, Any]:
    ego_road = site["ego_lane"]["road_id"]
    signs = [{**where, "value": landmark.get("value"), "unit": landmark.get("unit"), "road_id": landmark.get("road_id")}
             for landmark, where in _landmarks(document, located, (SPEED_SIGN,))]
    return {
        "opendrive_kmh": site.get("speed_limit_kmh"),
        "ego_road": [_round_all(item) for item in document.get("road_speeds") or [] if item.get("road_id") == ego_road],
        "signs": _by_along(signs),
    }


def _controls(document: dict[str, Any], site: dict[str, Any], located) -> dict[str, Any]:
    ego_road = site["ego_lane"]["road_id"]
    stops = [{**where, "kind": "stop" if landmark["type"] == STOP_SIGN else "yield", "road_id": landmark.get("road_id")}
             for landmark, where in _landmarks(document, located, (STOP_SIGN, YIELD_SIGN))]
    lights = [{
        **where, "opendrive_id": light.get("opendrive_id"),
        "controls_ego_road": any(lane.get("road_id") == ego_road for lane in light.get("affected_lanes") or []),
        "green_s": light.get("green_s"), "yellow_s": light.get("yellow_s"), "red_s": light.get("red_s"),
    } for light in document.get("traffic_lights") or [] if (where := located(light)) is not None]
    crossings = [where for crosswalk in document.get("crosswalks") or [] if (where := located(crosswalk.get("center")))]
    return {"traffic_lights": _by_along(lights), "stop_or_yield_signs": _by_along(stops), "crosswalks": _by_along(crossings)}


def _junctions(document: dict[str, Any], located) -> dict[str, Any]:
    junctions = []
    for junction in document.get("junctions") or []:
        extent = junction.get("extent") or {}
        if (where := located(junction.get("center"), max(extent.get("x", 0.0), extent.get("y", 0.0)))) is not None:
            junctions.append({**where, "junction_id": junction["junction_id"],
                              "incoming_road_count": junction.get("incoming_road_count"),
                              "four_way_candidate": junction.get("four_way_candidate")})
    return {"junctions": _by_along(junctions)}


def site_section(document: dict[str, Any], site_id: str, section: Section) -> dict[str, Any] | None:
    """One part of what lies on a site's corridor; along_m is measured from the ego anchor along its heading."""
    site = next((item for item in document.get("cut_in_sites") or [] if item["site_id"] == site_id), None)
    if site is None:
        return None
    if section == "geometry":
        return {"site_id": site_id, **{key: _round(site.get(key)) for key in GEOMETRY_KEYS}}
    located = _corridor(site)
    if section == "speed":
        return {"site_id": site_id, **_speed(document, site, located)}
    if section == "controls":
        return {"site_id": site_id, **_controls(document, site, located)}
    return {"site_id": site_id, **_junctions(document, located)}


def is_motorcycle(vehicle: dict[str, Any]) -> bool:
    base_type = (vehicle.get("base_type") or "").lower()
    if base_type:
        return base_type == "motorcycle"
    return vehicle.get("number_of_wheels") == 2 and any(hint in vehicle.get("id", "").lower() for hint in MOTORCYCLE_ID_HINTS)


def vehicle_kind(vehicle: dict[str, Any]) -> str | None:
    """car, van, truck, bus, motorcycle, bicycle (CARLA base_type, or inferred from the wheels when it is empty)."""
    base_type = (vehicle.get("base_type") or "").lower()
    if base_type:
        return base_type
    if vehicle.get("number_of_wheels") == 2:
        return "motorcycle" if is_motorcycle(vehicle) else "bicycle"
    return "car" if vehicle.get("number_of_wheels") == 4 else None


def list_vehicles(document: dict[str, Any], kind: Literal["all", "car", "motorcycle"], *, seed: int | None = None) -> dict[str, Any]:
    vehicles = [item for item in document.get("vehicles") or [] if kind == "all" or vehicle_kind(item) == kind]
    vehicles = sorted(vehicles, key=lambda item: item["id"]) if seed is None else _shuffled(vehicles, seed, lambda item: item["id"])
    return {
        "kind": kind, "total": len(vehicles),
        "vehicles": [{"id": item["id"], "kind": vehicle_kind(item), "length_m": _round(item.get("length_m")),
                      "width_m": _round(item.get("width_m"))} for item in vehicles[:MAX_VEHICLES]],
    }


def list_weather_presets(document: dict[str, Any]) -> dict[str, Any]:
    # "Default" is whatever the map was loaded with, not a weather: it cannot be described or reproduced.
    return {"weather_presets": sorted(name for name in document.get("weather_presets") or [] if name != "Default")}
