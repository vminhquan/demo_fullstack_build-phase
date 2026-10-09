"""Find conservative motorcycle cut-in corridors while CARLA's map API is available.

The exported site is a lower bound on continuously verified road length. We
only accept a source lane whose left/right neighbor is a same-direction
Driving lane throughout the corridor. This intentionally rejects ambiguous
branches and older waypoint-only catalogs rather than guessing topology.

A site needs MIN_CORRIDOR_M; its location tags describe those first metres (as
before catalog.v2), while available_length_m and the corridor facts follow the
verified pair up to MAX_CORRIDOR_M ahead and MAX_UPSTREAM_M behind the anchor.
A corridor that stops at a junction within APPROACH_MAX_M is a junction_approach
(the catalog adds intersection_4way when that junction has four incoming roads).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

SITE_SPACING_M = 20.0
CHECK_STEP_M = 5.0
MIN_CORRIDOR_M = 60.0
MAX_CORRIDOR_M = 300.0
MAX_UPSTREAM_M = 100.0
# Steps are 0.5..7.5 m: enough iterations to cover MAX_CORRIDOR_M even with short steps.
MAX_STEPS = 2 * math.ceil(MAX_CORRIDOR_M / CHECK_STEP_M) + 3
MAX_HEADING_DIFFERENCE_DEG = 20.0
CURVE_HEADING_CHANGE_DEG = 15.0
# A junction this far ahead of the anchor at most still makes the site an approach to it.
APPROACH_MAX_M = 150.0
JUNCTION_LOOKAHEAD_STEPS = 2


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
        # CARLA returns values such as -450.3; [-180, 180) like the catalog's other poses.
        "yaw": round((float(transform.rotation.yaw) + 180.0) % 360.0 - 180.0, 2),
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


def _predecessor(waypoint: Any) -> Any | None:
    try:
        previous_waypoints = waypoint.previous(CHECK_STEP_M)
    except (AttributeError, RuntimeError):
        return None
    return previous_waypoints[0] if len(previous_waypoints) == 1 else None


def _junction_id(waypoint: Any) -> int | None:
    value = getattr(waypoint, "junction_id", None)
    if value is None:
        try:
            value = waypoint.get_junction().id
        except (AttributeError, RuntimeError):
            return None
    return int(value) if int(value) >= 0 else None


def _junction_ahead(waypoint: Any) -> int | None:
    """Id of the junction the lane enters within a couple of steps after `waypoint`, if any."""
    frontier = [waypoint]
    for _ in range(JUNCTION_LOOKAHEAD_STEPS):
        following = []
        for item in frontier:
            try:
                following.extend(item.next(CHECK_STEP_M))
            except (AttributeError, RuntimeError):
                continue
        for item in following:
            if item.is_junction and (junction_id := _junction_id(item)) is not None:
                return junction_id
        frontier = following
    return None


def _crossing_allowed(source: Any, side: str) -> bool | None:
    """Whether the source lane's own rule lets a vehicle move to `side`; None when the CARLA build lacks lane_change."""
    rule = getattr(source, "lane_change", None)
    if rule is None:
        return None
    name = str(rule).rsplit(".", 1)[-1]
    return name == "Both" or name.lower() == side


def _marking_type(source: Any, side: str) -> str | None:
    marking = getattr(source, side + "_lane_marking", None)
    return None if marking is None else str(marking.type).rsplit(".", 1)[-1]


def _step_ok(current_source: Any, current_target: Any, next_source: Any, next_target: Any) -> tuple[bool, int]:
    """Both lanes advance by a similar, plausible distance with OpenDRIVE s moving the same way: (ok, s direction)."""
    source_step, target_step = _distance(current_source, next_source), _distance(current_target, next_target)
    if (
        source_step < 0.5 or target_step < 0.5
        or source_step > CHECK_STEP_M * 1.5 or target_step > CHECK_STEP_M * 1.5
        or abs(source_step - target_step) > CHECK_STEP_M / 2
    ):
        return False, 0
    source_s_step = float(next_source.s) - float(current_source.s)
    target_s_step = float(next_target.s) - float(current_target.s)
    if (abs(source_s_step) < 0.5 or abs(target_s_step) < 0.5
            or abs(source_s_step) > CHECK_STEP_M * 1.5
            or abs(target_s_step) > CHECK_STEP_M * 1.5
            or source_s_step * target_s_step <= 0):
        return False, 0
    return True, 1 if source_s_step > 0 else -1


def _verified_corridor(source: Any, target: Any, side: str) -> dict[str, Any] | None:
    """Walk the lane pair forward. None when it is not verified for MIN_CORRIDOR_M; otherwise its facts."""
    start = source
    start_source_ref, start_target_ref = _lane_ref(source), _lane_ref(target)
    current_source, current_target = source, target
    length = 0.0
    s_direction: int | None = None
    through_junction = False
    max_turn = 0.0
    tags: list[str] | None = None
    first_junction_m: float | None = None
    allowed: bool | None = True
    markings: set[str] = set()
    ends_at = "step_limit"
    for _ in range(MAX_STEPS):
        if not _pair_valid(current_source, current_target, side):
            ends_at = "lane_pair_ends"
            break
        if _lane_ref(current_source) != start_source_ref or _lane_ref(current_target) != start_target_ref:
            ends_at = "lane_pair_ends"
            break
        if current_source.is_junction or current_target.is_junction:
            through_junction = True
            if first_junction_m is None:
                first_junction_m = round(length, 2)
        max_turn = max(max_turn, _heading_difference(start, current_source))
        rule = _crossing_allowed(current_source, side)
        allowed = None if rule is None or allowed is None else allowed and rule
        marking = _marking_type(current_source, side)
        if marking:
            markings.add(marking)
        if tags is None and length >= MIN_CORRIDOR_M:
            if s_direction is None:
                return None
            tags = ["curve" if max_turn >= CURVE_HEADING_CHANGE_DEG else "straight"]
            if bool(source.is_junction and target.is_junction):
                tags.append("junction")
            elif through_junction:
                tags.append("junction_approach")
        if length >= MAX_CORRIDOR_M:
            ends_at = "max_length"
            break
        next_source, next_target = _successor(current_source), _successor(current_target)
        if next_source is None or next_target is None:
            ends_at = "branch_or_end"
            break
        ok, step_direction = _step_ok(current_source, current_target, next_source, next_target)
        if not ok or (s_direction is not None and step_direction != s_direction):
            ends_at = "discontinuity"
            break
        s_direction = step_direction
        length += min(_distance(current_source, next_source), _distance(current_target, next_target))
        current_source, current_target = next_source, next_target
    if tags is None or s_direction is None:
        return None
    ahead = None if ends_at == "max_length" else _junction_ahead(current_source)
    if ahead is not None and length <= APPROACH_MAX_M and "junction" not in tags and "junction_approach" not in tags:
        tags = [*tags, "junction_approach"]
    return {
        "length": round(length, 2), "tags": tags, "s_direction": s_direction, "ends_at": ends_at,
        "junction_ahead": None if ahead is None else {"junction_id": ahead, "distance_m": round(length, 2)},
        "max_turn": round(max_turn, 1), "first_junction_m": first_junction_m,
        "lane_change_allowed": allowed, "markings": sorted(markings),
    }


def _upstream_length(source: Any, target: Any, side: str, s_direction: int) -> float:
    """Verified length of the same lane pair behind the anchor (for a motorcycle starting behind the ego)."""
    start_refs = (_lane_ref(source), _lane_ref(target))
    current_source, current_target = source, target
    length = 0.0
    while length < MAX_UPSTREAM_M:
        previous_source, previous_target = _predecessor(current_source), _predecessor(current_target)
        if previous_source is None or previous_target is None or not _pair_valid(previous_source, previous_target, side):
            break
        if (_lane_ref(previous_source), _lane_ref(previous_target)) != start_refs:
            break
        ok, step_direction = _step_ok(previous_source, previous_target, current_source, current_target)
        if not ok or step_direction != s_direction:
            break
        length += min(_distance(previous_source, current_source), _distance(previous_target, current_target))
        current_source, current_target = previous_source, previous_target
    return round(min(length, MAX_UPSTREAM_M), 2)


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
            accepted.add(pair)
            site_id = ":".join(str(part) for part in (*pair[:4], round(float(source.s), 1)))
            sites.append({
                "site_id": site_id,
                "ego_lane": _lane_ref(target),
                "motorcycle_lane": _lane_ref(source),
                "ego_anchor": _pose(target),
                "motorcycle_anchor": _pose(source),
                "available_length_m": verified["length"],
                "location_tags": verified["tags"],
                "verification_source": "carla_topology",
                "ego_s": round(float(target.s), 3),
                "motorcycle_s": round(float(source.s), 3),
                "s_direction": verified["s_direction"],
                "target_side": side,
                "upstream_length_m": _upstream_length(source, target, side, verified["s_direction"]),
                "lane_change_allowed_throughout": verified["lane_change_allowed"],
                "marking_between": verified["markings"],
                "max_heading_change_deg": verified["max_turn"],
                "first_junction_m": verified["first_junction_m"],
                "ends_at": verified["ends_at"],
                "junction_ahead": verified["junction_ahead"],
                "lane_width_m": {"ego": round(float(target.lane_width), 2), "motorcycle": round(float(source.lane_width), 2)},
            })
    return sorted(sites, key=lambda item: item["site_id"])
