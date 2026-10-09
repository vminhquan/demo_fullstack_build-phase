"""Read-only answers for agent2's tool calls, computed from the light part of a stored catalog snapshot.

Answers are small (top-N, rounded) because they go into an LLM context. Unknown values stay null with a
reason instead of being guessed, and each fact says where it came from.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

MAX_SITES = 20
# Objects counted as "on the corridor": ahead of the ego anchor within the verified length, near the road.
WINDOW_MARGIN_M = 10.0
WINDOW_LATERAL_M = 20.0
SPEED_SIGN, STOP_SIGN, YIELD_SIGN = "274", "206", "205"

CONTEXT_NOTES = [
    "along_m/lateral_m are a straight-line projection from the ego anchor along its heading; approximate on curves.",
    "Traffic light timings are what CARLA reported; 10/3/2 s is CARLA's default, not a map-specific plan.",
    "speed_limit.opendrive_kmh comes from the OpenDRIVE road type; signs are reported as CARLA lists them (value + unit).",
]


def _round(value: Any) -> Any:
    return round(value, 1) if isinstance(value, float) else value


def _yaw(value: float) -> float:
    return round((value + 180.0) % 360.0 - 180.0, 1)


def compact_site(site: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: _round(site.get(key)) for key in (
            "site_id", "location_tags", "available_length_m", "upstream_length_m", "speed_limit_kmh", "target_side",
            "lane_change_allowed_throughout", "marking_between", "max_heading_change_deg", "first_junction_m", "ends_at",
            "lane_width_m",
        )
    }
    if compact["speed_limit_kmh"] is None:
        compact["speed_limit_unknown_reason"] = "No OpenDRIVE speed on this road (often a junction connector)."
    return compact


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
            and (near_junction is None or (site.get("first_junction_m") is not None) is near_junction)
        )

    matching = sorted(filter(keep, sites), key=lambda site: (-site.get("available_length_m", 0), site["site_id"]))
    speeds = Counter(site.get("speed_limit_kmh") for site in sites)
    return {
        "map_name": document.get("map_name"),
        "total_sites": len(sites),
        "total_matching": len(matching),
        "sites": [compact_site(site) for site in matching[: max(1, min(limit, MAX_SITES))]],
        # What the whole map offers, so the model can relax a filter that matched nothing.
        "map_summary": {
            "tags": dict(Counter(tag for site in sites for tag in site.get("location_tags") or [])),
            "speed_limits_kmh": {("unknown" if speed is None else str(speed)): count for speed, count in speeds.items()},
            "max_length_m": _round(max((site.get("available_length_m", 0.0) for site in sites), default=0.0)),
        },
    }


def _frame(anchor: dict[str, Any]):
    heading = math.radians(anchor["yaw"])
    cos, sin = math.cos(heading), math.sin(heading)

    def project(point: dict[str, Any]) -> tuple[float, float]:
        dx, dy = point["x"] - anchor["x"], point["y"] - anchor["y"]
        return dx * cos + dy * sin, abs(-dx * sin + dy * cos)

    return project


def site_context(document: dict[str, Any], site_id: str) -> dict[str, Any] | None:
    site = next((item for item in document.get("cut_in_sites") or [] if item["site_id"] == site_id), None)
    if site is None:
        return None
    project = _frame(site["ego_anchor"])
    start = -(site.get("upstream_length_m") or 0.0) - WINDOW_MARGIN_M
    end = site["available_length_m"] + WINDOW_MARGIN_M
    ego_road = site["ego_lane"]["road_id"]

    def located(point: dict[str, Any], reach: float = 0.0) -> dict[str, float] | None:
        """Along/lateral offsets of `point` if it is on the corridor; `reach` widens the window (junction size).
        Records stored without a position (CARLA left it out) are skipped."""
        if not point or point.get("x") is None or point.get("y") is None:
            return None
        along, lateral = project(point)
        if start - reach <= along <= end + reach and lateral <= WINDOW_LATERAL_M + reach:
            return {"along_m": round(along, 1), "lateral_m": round(lateral, 1)}
        return None

    signs, stops = [], []
    for landmark in document.get("landmarks") or []:
        if landmark.get("type") not in (SPEED_SIGN, STOP_SIGN, YIELD_SIGN) or (where := located(landmark)) is None:
            continue
        item = {**where, "name": landmark.get("name"), "road_id": landmark.get("road_id")}
        if landmark["type"] == SPEED_SIGN:
            signs.append({**item, "value": landmark.get("value"), "unit": landmark.get("unit")})
        else:
            stops.append({**item, "kind": "stop" if landmark["type"] == STOP_SIGN else "yield"})

    lights = []
    for light in document.get("traffic_lights") or []:
        if (where := located(light)) is None:
            continue
        lights.append({
            **where, "opendrive_id": light.get("opendrive_id"),
            "controls_ego_road": any(lane.get("road_id") == ego_road for lane in light.get("affected_lanes") or []),
            "green_s": light.get("green_s"), "yellow_s": light.get("yellow_s"), "red_s": light.get("red_s"),
        })

    crossings = [where for crosswalk in document.get("crosswalks") or [] if (where := located(crosswalk.get("center")))]
    junctions = []
    for junction in document.get("junctions") or []:
        extent = junction.get("extent") or {}
        if (where := located(junction.get("center"), max(extent.get("x", 0.0), extent.get("y", 0.0)))) is not None:
            junctions.append({**where, "junction_id": junction["junction_id"],
                              "incoming_road_count": junction.get("incoming_road_count"),
                              "four_way_candidate": junction.get("four_way_candidate")})

    by_along = lambda item: item["along_m"]  # noqa: E731
    return {
        "map_name": document.get("map_name"),
        "site": {
            **compact_site(site),
            "ego_lane": site["ego_lane"], "motorcycle_lane": site["motorcycle_lane"],
            "ego_anchor": {**site["ego_anchor"], "yaw": _yaw(site["ego_anchor"]["yaw"])},
            "motorcycle_anchor": {**site["motorcycle_anchor"], "yaw": _yaw(site["motorcycle_anchor"]["yaw"])},
        },
        "speed_limit": {
            "opendrive_kmh": site.get("speed_limit_kmh"),
            "ego_road": [_round_all(item) for item in document.get("road_speeds") or [] if item.get("road_id") == ego_road],
            "signs": sorted(signs, key=by_along),
        },
        "traffic_lights": sorted(lights, key=by_along),
        "stop_or_yield_signs": sorted(stops, key=by_along),
        "crosswalks": sorted(crossings, key=by_along),
        "junctions": sorted(junctions, key=by_along),
        "window_m": {"from": round(start, 1), "to": round(end, 1)},
        "notes": CONTEXT_NOTES,
    }


def _round_all(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _round(value) for key, value in item.items()}
