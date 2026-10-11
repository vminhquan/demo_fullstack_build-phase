"""Map questions the LLM asks while composing: where the ego can start, and what lies around a lane position.

Answered from the lane graph of the snapshot the run received (the same stored snapshot, by content hash), so
they cost no Backend round trip. Answers are small: lane references and a few numbers, no coordinates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.scenario.lanes import LaneGraph

SAMPLE_EVERY = 12  # waypoints (2 m apart): one candidate start every 24 m
LOOK_AHEAD_M = 300.0
MAX_STARTS = 20


@dataclass(frozen=True)
class Start:
    road_id: int
    lane_id: int
    s: float
    tags: tuple[str, ...]
    path_ahead_m: float
    junction_ahead_m: float | None
    junction_four_way: bool | None
    left_lanes: int
    right_lanes: int

    def as_dict(self) -> dict:
        return {"road_id": self.road_id, "lane_id": self.lane_id, "s": self.s, "tags": list(self.tags),
                "path_ahead_m": round(self.path_ahead_m), "junction_ahead_m": self.junction_ahead_m,
                "junction_ahead_four_way": self.junction_four_way,
                "left_lanes_same_direction": self.left_lanes, "right_lanes_same_direction": self.right_lanes}


def _count(graph: LaneGraph, point, side: str) -> int:
    count = 0
    while (point := graph.neighbor(point, side)) is not None and count < 6:
        count += 1
    return count


def describe(graph: LaneGraph, road_id: int, lane_id: int, s: float) -> Start | None:
    found = graph.locate(road_id, lane_id, s)
    if found is None:
        return None
    point = found.point
    path = graph.path(point.road_id, point.lane_id, point.s, ahead=LOOK_AHEAD_M)
    turn = path.heading_change(0.0, 120.0)
    ahead = path.junction_ahead(0.0, 150.0)
    junction, four_way = (ahead[0], graph.is_four_way(ahead[1])) if ahead else (None, None)
    tags = ["straight" if turn < 5 else "curve" if turn >= 15 else "gentle_curve"]
    if junction is not None and junction > 0:
        tags.append("junction_approach")
        if four_way:
            tags.append("intersection_4way_approach")
    if point.is_junction:
        tags.append("in_junction")
    left, right = _count(graph, point, "left"), _count(graph, point, "right")
    if left + right:
        tags.append("multi_lane")
    return Start(point.road_id, point.lane_id, round(point.s, 1), tuple(tags), path.length,
                 round(junction, 1) if junction is not None else None, four_way, left, right)


def _all_starts(graph: LaneGraph) -> list[Start]:
    cached = getattr(graph, "_starts", None)
    if cached is None:
        cached = []
        for points in graph.lanes.values():
            for point in points[::SAMPLE_EVERY]:
                if not point.is_junction and (start := describe(graph, point.road_id, point.lane_id, point.s)) is not None:
                    cached.append(start)
        graph._starts = cached  # type: ignore[attr-defined]
    return cached


def find_starts(graph: LaneGraph, *, tags: list[str] | None, min_path_m: float | None, min_left_lanes: int | None,
                min_right_lanes: int | None, seed: int | None, limit: int | None) -> dict:
    matching = [start for start in _all_starts(graph)
                if set(tags or []) <= set(start.tags)
                and start.path_ahead_m >= (min_path_m or 0)
                and start.left_lanes >= (min_left_lanes or 0) and start.right_lanes >= (min_right_lanes or 0)]
    # Stable per-seed order: parallel variants are offered different starts.
    matching.sort(key=lambda item: hashlib.sha256(f"{seed}:{item.road_id}:{item.lane_id}:{item.s}".encode()).hexdigest())
    count = max(1, min(limit or 10, MAX_STARTS))
    answer = {"total_matching": len(matching), "starts": [item.as_dict() for item in matching[:count]]}
    if not matching:
        tally: dict[str, int] = {}
        for start in _all_starts(graph):
            for tag in start.tags:
                tally[tag] = tally.get(tag, 0) + 1
        answer["map_tags"] = tally
        answer["hint"] = "No start matches every filter: drop the filters the user did not ask for and search again."
    return answer


def has_start(graph: LaneGraph, tags: set[str]) -> bool:
    """Is there one place where the ego can start with all these tags together?"""
    return any(tags <= set(start.tags) for start in _all_starts(graph))


def capabilities(graph: LaneGraph) -> dict[str, bool]:
    """What the map can host, alone and combined at one place: checked before accepting a prompt cannot be built."""
    return {
        "multi_lane": has_start(graph, {"multi_lane"}),
        "junction": has_start(graph, {"junction_approach"}),
        "four_way": has_start(graph, {"intersection_4way_approach"}),
        "multi_lane_before_junction": has_start(graph, {"multi_lane", "junction_approach"}),
        "multi_lane_before_four_way": has_start(graph, {"multi_lane", "intersection_4way_approach"}),
    }
