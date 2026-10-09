"""Find conservative motorcycle cut-in corridors while CARLA's map API is available.

The exported site is a lower bound on continuously verified road length. We
only accept a source lane whose left/right neighbor is a same-direction
Driving lane throughout the corridor. This intentionally rejects ambiguous
branches and older waypoint-only catalogs rather than guessing topology.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

SITE_SPACING_M = 20.0
CHECK_STEP_M = 5.0
MIN_CORRIDOR_M = 60.0
MAX_HEADING_DIFFERENCE_DEG = 20.0
CURVE_HEADING_CHANGE_DEG = 15.0


def _lane_ref(waypoint: Any) -> dict[str, int]:
    return {
        "road_id": int(waypoint.road_id),
        "section_id": int(waypoint.section_id),
        "lane_id": int(waypoint.lane_id),
    }


def _pose(waypoint: Any) -> dict[str, float]:
    transform = waypoint.transform
    return {
        "x": round(float(transform.location.x), 2),
        "y": round(float(transform.location.y), 2),
        "z": round(float(transform.location.z), 2),
        "yaw": round(float(transform.rotation.yaw), 2),
    }


def _distance(a: Any, b: Any) -> float:
    first, second = a.transform.location, b.transform.location
    return math.dist((first.x, first.y, first.z), (second.x, second.y, second.z))


def _heading_difference(a: Any, b: Any) -> float:
    delta = float(a.transform.rotation.yaw) - float(b.transform.rotation.yaw)
    return abs((delta + 180.0) % 360.0 - 180.0)


def _neighbor(source: Any, side: str) -> Any | None:
    method = getattr(source, f"get_{side}_lane", None)
    if method is None:
        return None
    try:
        return method()
    except RuntimeError:
        return None


def _pair_valid(source: Any, target: Any, side: str) -> bool:
    if source is None or target is None:
        return False
    if str(source.lane_type).rsplit(".", 1)[-1] != "Driving":
        return False
    if str(target.lane_type).rsplit(".", 1)[-1] != "Driving":
        return False
    source_ref, target_ref = _lane_ref(source), _lane_ref(target)
    if source_ref["road_id"] != target_ref["road_id"] or source_ref["section_id"] != target_ref["section_id"]:
        return False
    if source_ref["lane_id"] * target_ref["lane_id"] <= 0 or source_ref["lane_id"] == target_ref["lane_id"]:
        return False
    if _heading_difference(source, target) > MAX_HEADING_DIFFERENCE_DEG:
        return False
    source_pos, target_pos = source.transform.location, target.transform.location
    if abs(float(source_pos.z) - float(target_pos.z)) > 1.0:
        return False
    lane_width = (float(source.lane_width) + float(target.lane_width)) / 2.0
    if lane_width <= 0 or not 0.6 * lane_width <= _distance(source, target) <= 1.6 * lane_width:
        return False
    adjacent = _neighbor(source, side)
    return adjacent is not None and _lane_ref(adjacent) == target_ref


def _successor(waypoint: Any) -> Any | None:
    try:
        next_waypoints = waypoint.next(CHECK_STEP_M)
    except (AttributeError, RuntimeError):
        return None
    return next_waypoints[0] if len(next_waypoints) == 1 else None


def _verified_corridor(source: Any, target: Any, side: str) -> tuple[float, list[str], int] | None:
    start = source
    start_source_ref, start_target_ref = _lane_ref(source), _lane_ref(target)
    current_source, current_target = source, target
    length = 0.0
    s_direction: int | None = None
    through_junction = False
    max_turn = 0.0
    for _ in range(math.ceil(MIN_CORRIDOR_M / CHECK_STEP_M) + 3):
        if not _pair_valid(current_source, current_target, side):
            return None
        if _lane_ref(current_source) != start_source_ref or _lane_ref(current_target) != start_target_ref:
            return None
        through_junction |= bool(current_source.is_junction or current_target.is_junction)
        max_turn = max(max_turn, _heading_difference(start, current_source))
        if length >= MIN_CORRIDOR_M:
            if s_direction is None:
                return None
            tags = ["curve" if max_turn >= CURVE_HEADING_CHANGE_DEG else "straight"]
            if bool(source.is_junction and target.is_junction):
                tags.append("junction")
            elif through_junction:
                tags.append("junction_approach")
            return round(length, 2), tags, s_direction
        next_source, next_target = _successor(current_source), _successor(current_target)
        if next_source is None or next_target is None:
            return None
        source_step, target_step = _distance(current_source, next_source), _distance(current_target, next_target)
        if (
            source_step < 0.5 or target_step < 0.5
            or source_step > CHECK_STEP_M * 1.5 or target_step > CHECK_STEP_M * 1.5
            or abs(source_step - target_step) > CHECK_STEP_M / 2
        ):
            return None
        source_s_step = float(next_source.s) - float(current_source.s)
        target_s_step = float(next_target.s) - float(current_target.s)
        if (abs(source_s_step) < 0.5 or abs(target_s_step) < 0.5
                or abs(source_s_step) > CHECK_STEP_M * 1.5
                or abs(target_s_step) > CHECK_STEP_M * 1.5
                or source_s_step * target_s_step <= 0):
            return None
        step_direction = 1 if source_s_step > 0 else -1
        if s_direction is not None and step_direction != s_direction:
            return None
        s_direction = step_direction
        length += min(source_step, target_step)
        current_source, current_target = next_source, next_target
    return None


def extract_cut_in_sites(waypoints: Iterable[Any]) -> list[dict[str, Any]]:
    """Return serializable verified sites from CARLA waypoint objects.

    An entry is oriented from the motorcycle's source lane into the ego lane.
    ``junction`` is a generic CARLA junction, never proof of a four-way road.
    """

    sites: list[dict[str, Any]] = []
    accepted: set[tuple[int, int, int, int, int]] = set()
    for source in waypoints:
        if str(source.lane_type).rsplit(".", 1)[-1] != "Driving":
            continue
        for side in ("left", "right"):
            target = _neighbor(source, side)
            if not _pair_valid(source, target, side):
                continue
            pair = (
                int(source.road_id), int(source.section_id), int(source.lane_id),
                int(target.lane_id), math.floor(float(source.s) / SITE_SPACING_M),
            )
            if pair in accepted:
                continue
            verified = _verified_corridor(source, target, side)
            if verified is None:
                continue
            length, tags, s_direction = verified
            accepted.add(pair)
            site_id = ":".join(str(part) for part in (*pair[:4], round(float(source.s), 1)))
            sites.append({
                "site_id": site_id,
                "ego_lane": _lane_ref(target),
                "motorcycle_lane": _lane_ref(source),
                "ego_anchor": _pose(target),
                "motorcycle_anchor": _pose(source),
                "available_length_m": length,
                "location_tags": tags,
                "verification_source": "carla_topology",
                "ego_s": round(float(target.s), 3),
                "motorcycle_s": round(float(source.s), 3),
                "s_direction": s_direction,
                "target_side": side,
            })
    return sorted(sites, key=lambda item: item["site_id"])
