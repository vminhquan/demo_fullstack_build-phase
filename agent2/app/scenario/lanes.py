"""Lane graph built from the snapshot's driving waypoints (every 2 m) and CARLA topology.

A lane is the waypoints of one (road, section, lane) in travel direction: OpenDRIVE lanes with a negative id run
along increasing s, positive ids against it. Driving on continues into the next section of the same road, then
through the topology links; at a fork the straightest branch is taken. A `Path` is such a chain of waypoints with
cumulative distances, used to place actors, simulate them and measure gaps along the ego's way.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass

from app.catalog.models import CatalogV1, LaneWaypoint, NeighborLane

LaneKey = tuple[int, int, int]  # road, section, lane
LOCATE_TOLERANCE_M = 2.5


def travel_sign(lane_id: int) -> int:
    """+1 when the lane runs along increasing s."""
    return 1 if lane_id < 0 else -1


def _yaw_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class Located:
    key: LaneKey
    index: int
    point: LaneWaypoint


class Path:
    """Waypoints in travel order with cumulative distance; `origin` is the distance of the requested start."""

    def __init__(self, points: list[LaneWaypoint], origin: float = 0.0):
        self.points = points
        self.dist = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            self.dist.append(self.dist[-1] + math.hypot(b.x - a.x, b.y - a.y))
        self.length = self.dist[-1]
        self.origin = origin

    def _segment(self, d: float) -> tuple[int, float]:
        d = min(max(d, 0.0), self.length)
        i = max(0, min(bisect.bisect_right(self.dist, d) - 1, len(self.points) - 2))
        span = self.dist[i + 1] - self.dist[i] if len(self.points) > 1 else 0.0
        return i, (d - self.dist[i]) / span if span > 0 else 0.0

    def at(self, d: float) -> tuple[float, float]:
        """World x, y at distance `d` along the path (clamped to its ends)."""
        if len(self.points) == 1:
            return self.points[0].x, self.points[0].y
        i, u = self._segment(d)
        a, b = self.points[i], self.points[i + 1]
        return a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u

    def point_at(self, d: float) -> LaneWaypoint:
        i, u = self._segment(d)
        return self.points[min(i + (u >= 0.5), len(self.points) - 1)]

    def project(self, x: float, y: float) -> tuple[float, float]:
        """(distance along the path, absolute lateral offset) of a world point."""
        best = (math.inf, 0.0)
        for i in range(len(self.points) - 1):
            a, b = self.points[i], self.points[i + 1]
            dx, dy = b.x - a.x, b.y - a.y
            span = dx * dx + dy * dy
            u = 0.0 if span == 0 else min(max(((x - a.x) * dx + (y - a.y) * dy) / span, 0.0), 1.0)
            px, py = a.x + dx * u, a.y + dy * u
            lateral = math.hypot(x - px, y - py)
            if lateral < best[0]:
                best = (lateral, self.dist[i] + u * math.sqrt(span))
        return best[1], best[0]

    def heading_change(self, start: float, end: float) -> float:
        """Largest yaw change (degrees) from the yaw at `start`, over [start, end]."""
        base = self.point_at(start).yaw
        lo, hi = bisect.bisect_left(self.dist, start), bisect.bisect_right(self.dist, end)
        return max((_yaw_diff(point.yaw, base) for point in self.points[lo:hi]), default=0.0)

    def first_junction(self, start: float, end: float) -> float | None:
        found = self.junction_ahead(start, end)
        return found[0] if found else None

    def junction_ahead(self, start: float, end: float) -> tuple[float, int | None] | None:
        """(distance from `start`, junction id) of the first junction waypoint in [start, end]."""
        lo, hi = bisect.bisect_left(self.dist, start), bisect.bisect_right(self.dist, end)
        return next(((self.dist[i] - start, self.points[i].junction_id) for i in range(lo, hi) if self.points[i].is_junction), None)


class LaneGraph:
    def __init__(self, catalog: CatalogV1):
        raw: dict[LaneKey, list[LaneWaypoint]] = defaultdict(list)
        for point in catalog.waypoints:
            if point.lane_type == "Driving":
                raw[(point.road_id, point.section_id, point.lane_id)].append(point)
        self.lanes = {key: sorted(points, key=lambda item: item.s * travel_sign(key[2])) for key, points in raw.items()}
        # Per lane: (s, index) sorted by s, for nearest-s lookups.
        self._s_index = {key: sorted((point.s, index) for index, point in enumerate(points)) for key, points in self.lanes.items()}
        self._by_road_lane: dict[tuple[int, int], list[LaneKey]] = defaultdict(list)
        for key in self.lanes:
            self._by_road_lane[(key[0], key[2])].append(key)
        for keys in self._by_road_lane.values():
            keys.sort(key=lambda key: key[1] * travel_sign(key[2]))
        self.junctions = {item.junction_id: item for item in catalog.junctions or []}
        self._next: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        self._prev: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for link in catalog.topology or []:
            start, end = (link.start.road_id, link.start.lane_id), (link.end.road_id, link.end.lane_id)
            if start != end and end not in self._next[start]:
                self._next[start].append(end)
                self._prev[end].append(start)

    def locate(self, road_id: int, lane_id: int, s: float) -> Located | None:
        """The driving waypoint of (road, lane) nearest to s, within LOCATE_TOLERANCE_M."""
        best: Located | None = None
        for key in self._by_road_lane.get((road_id, lane_id), []):
            index = self._s_index[key]
            at = bisect.bisect_left(index, (s, -1))
            for value, i in index[max(0, at - 1): at + 1]:
                point = self.lanes[key][i]
                if abs(value - s) <= LOCATE_TOLERANCE_M and (best is None or abs(value - s) < abs(best.point.s - s)):
                    best = Located(key, i, point)
        return best

    def neighbor(self, point: LaneWaypoint, side: str) -> LaneWaypoint | None:
        """The waypoint in the same-direction driving lane on `side` ("left"/"right" of travel), if any."""
        ref: NeighborLane | None = point.left_lane if side == "left" else point.right_lane
        if ref is None or (ref.lane_type not in (None, "Driving")) or travel_sign(ref.lane_id) != travel_sign(point.lane_id):
            return None
        found = self.locate(ref.road_id, ref.lane_id, ref.s)
        return found.point if found else None

    def is_four_way(self, junction_id: int | None) -> bool:
        info = self.junctions.get(junction_id) if junction_id is not None else None
        return bool(info and (info.four_way_candidate or (info.incoming_road_count or 0) >= 4))

    def offset_lane(self, point: LaneWaypoint, lane_offset: int) -> LaneWaypoint | None:
        """Walk `lane_offset` lanes sideways: positive to the left of travel, negative to the right."""
        for _ in range(abs(lane_offset)):
            point = self.neighbor(point, "left" if lane_offset > 0 else "right")
            if point is None:
                return None
        return point

    def _straightest(self, heading: float, candidates: list[LaneKey], *, forward: bool) -> LaneKey | None:
        def deviation(key: LaneKey) -> float:
            points = self.lanes[key]
            probe = points[min(len(points) - 1, 8)] if forward else points[max(0, len(points) - 9)]
            return _yaw_diff(probe.yaw, heading)
        return min(candidates, key=deviation, default=None)

    def _step(self, key: LaneKey, *, forward: bool) -> LaneKey | None:
        road, section, lane = key
        sections = self._by_road_lane[(road, lane)]
        position = sections.index(key) + (1 if forward else -1)
        if 0 <= position < len(sections):
            return sections[position]
        links = (self._next if forward else self._prev).get((road, lane), [])
        candidates = [self._by_road_lane[link][0 if forward else -1] for link in links if self._by_road_lane.get(link)]
        points = self.lanes[key]
        return self._straightest(points[-1 if forward else 0].yaw, candidates, forward=forward)

    def _walk(self, start: Located, length: float, *, forward: bool) -> list[LaneWaypoint]:
        key, points = start.key, self.lanes[start.key]
        chain = points[start.index:] if forward else list(reversed(points[: start.index + 1]))
        travelled = sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(chain, chain[1:], strict=False))
        seen = {key}
        while travelled < length:
            key = self._step(key, forward=forward)
            if key is None or key in seen:
                break
            seen.add(key)
            more = self.lanes[key] if forward else list(reversed(self.lanes[key]))
            # Skip a first point repeating the last one (lanes meet at the same spot).
            if more and math.hypot(more[0].x - chain[-1].x, more[0].y - chain[-1].y) < 0.5:
                more = more[1:]
            for point in more:
                travelled += math.hypot(point.x - chain[-1].x, point.y - chain[-1].y)
                chain.append(point)
        # Keep only `length` (plus the point reaching it): the start lane alone may be much longer.
        total = 0.0
        for index in range(1, len(chain)):
            total += math.hypot(chain[index].x - chain[index - 1].x, chain[index].y - chain[index - 1].y)
            if total >= length:
                return chain[: index + 1]
        return chain

    def path(self, road_id: int, lane_id: int, s: float, ahead: float, behind: float = 0.0) -> Path | None:
        start = self.locate(road_id, lane_id, s)
        if start is None:
            return None
        back = self._walk(start, behind, forward=False)[1:] if behind > 0 else []
        points = list(reversed(back)) + self._walk(start, ahead, forward=True)
        origin = Path(list(reversed(back)) + [start.point]).length if back else 0.0
        return Path(points, origin)


_CACHE: dict[str, LaneGraph] = {}


def lane_graph(content_hash: str, catalog: CatalogV1) -> LaneGraph:
    """One graph per snapshot content, reused across runs (snapshots are immutable by content hash)."""
    graph = _CACHE.get(content_hash)
    if graph is None:
        if len(_CACHE) >= 8:
            _CACHE.pop(next(iter(_CACHE)))
        graph = _CACHE[content_hash] = LaneGraph(catalog)
    return graph
