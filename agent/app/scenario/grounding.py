"""
Ground a ScenarioIR onto the real lanes of a catalog.v1 map.

The IR only says *where* actors are relative to the ego ("ahead_adjacent_left", 25 m). This module
turns that into concrete CARLA poses by walking the catalog's driving-lane waypoints:

  1. pick an ego spawn point whose lane matches the IR road type (straight run, junction ahead,
     curve) and offers the lanes the actors need (adjacent same-direction lane, oncoming lane);
  2. place every actor on a real lane at the requested arc distance along the ego's path;
  3. pick blueprints that exist in the catalog.

Coordinates stay in CARLA convention (left-handed: x forward, y right, yaw clockwise in degrees).
Anything that cannot be placed on a lane falls back to geometry and is reported in `warnings`,
so the caller never mistakes a guessed pose for a map-grounded one.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.contracts.catalog_v1 import CatalogV1, LaneWaypoint
from app.scenario.schemas import ActorType, RelativePosition, ScenarioIR

LANE_STEP_MAX_M = 4.5  # catalog waypoints are sampled every ~2 m
VEHICLE_Z_OFFSET_M = 0.8
WALKER_Z_OFFSET_M = 1.0

PREFERRED_BLUEPRINTS: dict[str, tuple[str, ...]] = {
    "ego": ("vehicle.tesla.model3", "vehicle.lincoln.mkz_2020", "vehicle.audi.etron"),
    ActorType.CAR.value: ("vehicle.audi.a2", "vehicle.toyota.prius", "vehicle.citroen.c3", "vehicle.tesla.model3"),
    ActorType.MOTORCYCLE.value: ("vehicle.yamaha.yzf", "vehicle.kawasaki.ninja", "vehicle.vespa.zx125"),
    ActorType.BICYCLE.value: ("vehicle.bh.crossbike", "vehicle.diamondback.century", "vehicle.gazelle.omafiets"),
    ActorType.TRUCK.value: ("vehicle.carlamotors.european_hgv", "vehicle.carlamotors.carlacola", "vehicle.tesla.cybertruck"),
    ActorType.PEDESTRIAN.value: ("walker.pedestrian.0001", "walker.pedestrian.0002"),
}
BASE_TYPES: dict[str, frozenset[str]] = {
    "ego": frozenset({"car"}),
    ActorType.CAR.value: frozenset({"car", "van"}),
    ActorType.MOTORCYCLE.value: frozenset({"motorcycle"}),
    ActorType.BICYCLE.value: frozenset({"bicycle"}),
    ActorType.TRUCK.value: frozenset({"truck", "bus"}),
}

INTERSECTION_ROADS = {"intersection_4way", "intersection_3way", "roundabout"}
STRAIGHT_ROADS = {"highway_straight", "highway_merge", "urban_straight", "parking_lot"}


class GroundingError(ValueError):
    """The catalog cannot host this scenario (missing blueprint or no usable lane at all)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _wrap(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def heading_diff(a: float, b: float) -> float:
    return abs(_wrap(a - b))


def forward(yaw: float) -> tuple[float, float]:
    rad = math.radians(yaw)
    return math.cos(rad), math.sin(rad)


def right_of(yaw: float) -> tuple[float, float]:
    fx, fy = forward(yaw)
    return -fy, fx


def short_map_name(name: str) -> str:
    return name.rsplit("/", 1)[-1]


class LaneIndex:
    """Spatial hash over driving-lane waypoints with lane-following helpers."""

    def __init__(self, waypoints: list[LaneWaypoint], cell_m: float = 8.0) -> None:
        self.points = [item for item in waypoints if item.lane_type == "Driving"]
        if not self.points:
            raise GroundingError("CATALOG_NO_DRIVING_LANES", "Catalog has no driving-lane waypoints")
        self.cell = cell_m
        self.grid: dict[tuple[int, int], list[LaneWaypoint]] = defaultdict(list)
        for item in self.points:
            self.grid[self._key(item.x, item.y)].append(item)

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return int(math.floor(x / self.cell)), int(math.floor(y / self.cell))

    def near(self, x: float, y: float, radius: float) -> list[tuple[float, LaneWaypoint]]:
        cx, cy = self._key(x, y)
        span = int(math.ceil(radius / self.cell))
        found = []
        for gx in range(cx - span, cx + span + 1):
            for gy in range(cy - span, cy + span + 1):
                for item in self.grid.get((gx, gy), ()):
                    distance = math.hypot(item.x - x, item.y - y)
                    if distance <= radius:
                        found.append((distance, item))
        found.sort(key=lambda pair: (pair[0], pair[1].road_id, pair[1].lane_id, pair[1].s))
        return found

    def nearest(self, x: float, y: float, *, heading: float | None = None, max_heading_diff: float = 45.0, radius: float = 6.0) -> LaneWaypoint | None:
        for _, item in self.near(x, y, radius):
            if heading is None or heading_diff(item.yaw, heading) <= max_heading_diff:
                return item
        return None

    def step(self, current: LaneWaypoint, direction: int, visited: set[int]) -> LaneWaypoint | None:
        """Next waypoint ~2 m ahead (direction=1) or behind (-1) on the same lane, crossing road boundaries."""
        fx, fy = forward(current.yaw)
        rx, ry = right_of(current.yaw)
        best: tuple[float, LaneWaypoint] | None = None
        for _, item in self.near(current.x, current.y, LANE_STEP_MAX_M):
            if id(item) in visited or heading_diff(item.yaw, current.yaw) > 35.0:
                continue
            dx, dy = item.x - current.x, item.y - current.y
            along = (dx * fx + dy * fy) * direction
            lateral = abs(dx * rx + dy * ry)
            if along < 0.5 or lateral > 1.2:
                continue
            same_lane = (item.road_id, item.lane_id) == (current.road_id, current.lane_id)
            score = abs(along - 2.0) + 2.0 * lateral + (0.0 if same_lane else 0.3)
            if best is None or score < best[0]:
                best = (score, item)
        return None if best is None else best[1]

    def walk(self, start: LaneWaypoint, distance: float, direction: int = 1) -> tuple[list[LaneWaypoint], float]:
        """Follow the lane for `distance` metres. Returns the path and the arc length actually covered."""
        path, travelled, visited = [start], 0.0, {id(start)}
        current = start
        while travelled < distance:
            following = self.step(current, direction, visited)
            if following is None:
                break
            travelled += math.hypot(following.x - current.x, following.y - current.y)
            visited.add(id(following))
            path.append(following)
            current = following
        return path, travelled

    def neighbor(self, wp: LaneWaypoint, side: int, *, opposite: bool, max_lanes: int = 3) -> LaneWaypoint | None:
        """Adjacent lane on `side` (-1 left, +1 right), same or opposite travel direction."""
        rx, ry = right_of(wp.yaw)
        for k in range(1, max_lanes + 1):
            tx = wp.x + rx * side * wp.lane_width * k
            ty = wp.y + ry * side * wp.lane_width * k
            candidate = None
            for _, item in self.near(tx, ty, wp.lane_width * 0.6):
                if (item.road_id, item.lane_id) == (wp.road_id, wp.lane_id):
                    continue
                candidate = item
                break
            if candidate is None:
                return None
            diff = heading_diff(candidate.yaw, wp.yaw)
            if not opposite:
                return candidate if diff < 30.0 else None
            if diff > 150.0:
                return candidate
            if diff >= 30.0:
                return None
            # A same-direction lane sits between the ego and the oncoming lane; look one lane further.
        return None


@dataclass
class EgoCandidate:
    spawn_index: int
    lane: LaneWaypoint
    path: list[LaneWaypoint]
    path_length: float
    yaw_change: float
    junction_at: float | None
    has_left_same: bool
    has_right_same: bool
    has_oncoming: bool
    score: float = 0.0
    issues: list[str] = field(default_factory=list)


def _path_features(path: list[LaneWaypoint]) -> tuple[float, float | None]:
    start_yaw = path[0].yaw
    yaw_change, arc, junction_at = 0.0, 0.0, None
    for previous, item in zip(path, path[1:]):
        arc += math.hypot(item.x - previous.x, item.y - previous.y)
        yaw_change = max(yaw_change, heading_diff(item.yaw, start_yaw))
        if junction_at is None and item.is_junction:
            junction_at = arc
    return yaw_change, junction_at


@dataclass
class MapProfile:
    map_name: str
    road_types: list[str]
    has_multilane_same_direction: bool
    has_lanes_both_sides: bool
    has_oncoming_lane: bool
    max_straight_m: float
    spawn_points: int
    vehicles: int
    walkers: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_name": self.map_name,
            "road_types": self.road_types,
            "has_multilane_same_direction": self.has_multilane_same_direction,
            "has_lanes_both_sides": self.has_lanes_both_sides,
            "has_oncoming_lane": self.has_oncoming_lane,
            "max_straight_m": round(self.max_straight_m, 1),
            "spawn_points": self.spawn_points,
            "vehicles": self.vehicles,
            "walkers": self.walkers,
        }

    def prompt_text(self) -> str:
        return (
            f"CARLA map in use: {self.map_name} (fixed, do not choose another map). "
            f"Road types this map can host: {', '.join(self.road_types) or 'none'}. "
            f"Longest straight run: {self.max_straight_m:.0f} m. "
            f"Multi-lane same-direction roads: {'yes' if self.has_multilane_same_direction else 'no'}. "
            f"Two-way roads with an oncoming lane: {'yes' if self.has_oncoming_lane else 'no'}. "
            + ("" if self.has_lanes_both_sides else
               "No road has same-direction lanes on BOTH sides of the ego: never use ahead_adjacent_left and "
               "ahead_adjacent_right in the same scenario. ")
            + "Choose road_type and actor positions this map can host; keep actor distances below the longest straight run."
        )


class CatalogGrounder:
    """Grounds scenarios against one catalog snapshot. Build once per catalog, reuse per request."""

    PROBE_DISTANCE_M = 120.0

    def __init__(self, catalog: CatalogV1) -> None:
        self.catalog = catalog
        self.map_name = short_map_name(catalog.map_name)
        self.index = LaneIndex(catalog.waypoints)
        self._probes: list[EgoCandidate] | None = None

    # ------------------------------------------------------------------ candidates
    def _candidate(self, spawn_index: int, probe_distance: float) -> EgoCandidate | None:
        spawn = self.catalog.spawn_points[spawn_index]
        lane = self.index.nearest(spawn.x, spawn.y, heading=spawn.yaw, max_heading_diff=30.0, radius=3.0)
        if lane is None or lane.is_junction:
            return None
        path, length = self.index.walk(lane, probe_distance)
        yaw_change, junction_at = _path_features(path)
        return EgoCandidate(
            spawn_index=spawn_index,
            lane=lane,
            path=path,
            path_length=length,
            yaw_change=yaw_change,
            junction_at=junction_at,
            has_left_same=self.index.neighbor(lane, -1, opposite=False) is not None,
            has_right_same=self.index.neighbor(lane, 1, opposite=False) is not None,
            has_oncoming=self.index.neighbor(lane, -1, opposite=True) is not None,
        )

    def probes(self) -> list[EgoCandidate]:
        if self._probes is None:
            found = (self._candidate(i, self.PROBE_DISTANCE_M) for i in range(len(self.catalog.spawn_points)))
            self._probes = [item for item in found if item is not None]
        return self._probes

    def profile(self) -> MapProfile:
        probes = self.probes()
        straight = [p for p in probes if p.yaw_change < 12.0]
        road_types = []
        if any(p.junction_at is not None and p.junction_at >= 10.0 for p in probes):
            road_types += ["intersection_4way", "intersection_3way"]
        if any(p.path_length >= 60.0 for p in straight):
            road_types.append("urban_straight")
        if any(p.path_length >= 100.0 and (p.has_left_same or p.has_right_same) for p in straight):
            road_types += ["highway_straight", "highway_merge"]
        if any(p.yaw_change >= 30.0 for p in probes):
            road_types.append("urban_curve")
        max_straight = 0.0
        for p in straight:
            max_straight = max(max_straight, p.path_length if p.junction_at is None else p.junction_at)
        return MapProfile(
            map_name=self.map_name,
            road_types=road_types,
            has_multilane_same_direction=any(p.has_left_same or p.has_right_same for p in probes),
            has_lanes_both_sides=any(p.has_left_same and p.has_right_same for p in probes),
            has_oncoming_lane=any(p.has_oncoming for p in probes),
            max_straight_m=max_straight,
            spawn_points=len(self.catalog.spawn_points),
            vehicles=len(self.catalog.vehicles),
            walkers=len(self.catalog.walkers),
        )

    @staticmethod
    def _required_ahead(ir: ScenarioIR) -> float:
        ahead = [a.initial_distance_m for a in ir.actors if a.relative_position != RelativePosition.BEHIND_SAME_LANE]
        return (max(ahead) if ahead else 20.0) + 15.0

    @staticmethod
    def _primary_distance(ir: ScenarioIR) -> float:
        triggered = [a for a in ir.actors if a.trigger is not None] or list(ir.actors)
        return triggered[0].initial_distance_m

    def _score(self, cand: EgoCandidate, ir: ScenarioIR, needed: float) -> EgoCandidate:
        issues: list[str] = []
        score = 0.0
        road = ir.ego.road_type
        positions = {a.relative_position for a in ir.actors}
        if cand.path_length < needed - 1.0:
            score += 100.0 + (needed - cand.path_length)
            issues.append(f"lane ahead is only {cand.path_length:.0f} m (needs {needed:.0f} m)")
        if road in INTERSECTION_ROADS:
            target = self._primary_distance(ir)
            if cand.junction_at is None or cand.junction_at < 8.0:
                score += 200.0
                issues.append("no junction ahead of the ego lane")
            else:
                score += abs(cand.junction_at - target)
        elif road == "urban_curve":
            score += max(0.0, 30.0 - cand.yaw_change) * 3.0 + (10.0 if cand.junction_at is not None else 0.0)
            if cand.yaw_change < 30.0:
                issues.append("ego lane has no curve within reach")
        else:
            score += cand.yaw_change * 2.0
            if cand.junction_at is not None and cand.junction_at < needed:
                score += 25.0
            if road.startswith("highway") and not (cand.has_left_same or cand.has_right_same):
                score += 40.0
        if RelativePosition.AHEAD_ADJACENT_LEFT in positions and not cand.has_left_same:
            score += 80.0
            issues.append("no same-direction lane on the left")
        if RelativePosition.AHEAD_ADJACENT_RIGHT in positions and not cand.has_right_same:
            score += 80.0
            issues.append("no same-direction lane on the right")
        if RelativePosition.ONCOMING in positions and not cand.has_oncoming:
            score += 80.0
            issues.append("no oncoming lane")
        for act in ir.actors:
            crossing = act.relative_position in (RelativePosition.CROSSING_FROM_LEFT, RelativePosition.CROSSING_FROM_RIGHT)
            if crossing and act.actor_type not in (ActorType.PEDESTRIAN, ActorType.BICYCLE):
                side = -1 if act.relative_position == RelativePosition.CROSSING_FROM_LEFT else 1
                anchor, _ = self._point_at(cand.path, act.initial_distance_m)
                if self._crossing_lane(anchor, side) is None:
                    score += 80.0
                    issues.append(f"no crossing lane from the {'left' if side < 0 else 'right'}")
        cand.score, cand.issues = score, issues
        return cand

    def select_ego(self, ir: ScenarioIR, seed: int | None = None) -> EgoCandidate:
        needed = self._required_ahead(ir)
        pool = []
        for probe in self.probes():
            cand = probe
            if needed > self.PROBE_DISTANCE_M:
                cand = self._candidate(probe.spawn_index, needed) or probe
            pool.append(self._score(cand, ir, needed))
        if not pool:
            raise GroundingError("CATALOG_NO_SPAWN_ON_LANE", "No catalog spawn point lies on a driving lane")
        pool.sort(key=lambda item: (round(item.score, 3), item.spawn_index))
        if seed is None:
            return pool[0]
        # Rotate among equally good candidates so regenerating gives variety without losing quality.
        best = pool[0].score
        top = [item for item in pool[:8] if item.score <= best + 5.0]
        return top[seed % len(top)]

    # ------------------------------------------------------------------ blueprints
    def blueprint(self, role: str) -> str:
        if role == ActorType.PEDESTRIAN.value:
            available = sorted(item.id for item in self.catalog.walkers)
            for preferred in PREFERRED_BLUEPRINTS[role]:
                if preferred in available:
                    return preferred
            if available:
                return available[0]
            raise GroundingError("CATALOG_MISSING_BLUEPRINT", "Catalog has no walker blueprint for a pedestrian actor")
        ids = {item.id: item for item in self.catalog.vehicles}
        for preferred in PREFERRED_BLUEPRINTS[role]:
            if preferred in ids:
                return preferred
        matching = sorted(item.id for item in self.catalog.vehicles if (item.base_type or "").lower() in BASE_TYPES[role])
        if matching:
            return matching[0]
        raise GroundingError("CATALOG_MISSING_BLUEPRINT", f"Catalog has no vehicle blueprint for actor type '{role}'")

    # ------------------------------------------------------------------ placement
    def _point_at(self, path: list[LaneWaypoint], distance: float) -> tuple[LaneWaypoint, float]:
        arc = 0.0
        for previous, item in zip(path, path[1:]):
            arc += math.hypot(item.x - previous.x, item.y - previous.y)
            if arc >= distance:
                return item, arc
        return path[-1], arc

    def _edge_offset(self, wp: LaneWaypoint, side: int) -> float:
        """Distance from the lane centre to just beyond the outermost driving lane on `side`."""
        rx, ry = right_of(wp.yaw)
        offset = wp.lane_width * 0.5
        for k in range(1, 5):
            tx = wp.x + rx * side * wp.lane_width * k
            ty = wp.y + ry * side * wp.lane_width * k
            if not self.index.near(tx, ty, wp.lane_width * 0.6):
                break
            offset = wp.lane_width * (k + 0.5)
        return offset + 1.5

    def _crossing_lane(self, conflict: LaneWaypoint, side: int) -> LaneWaypoint | None:
        heading = conflict.yaw + (90.0 if side < 0 else -90.0)
        fx, fy = forward(conflict.yaw)
        rx, ry = right_of(conflict.yaw)
        best: tuple[float, LaneWaypoint] | None = None
        for _, item in self.index.near(conflict.x, conflict.y, 45.0):
            if heading_diff(item.yaw, heading) > 30.0:
                continue
            dx, dy = item.x - conflict.x, item.y - conflict.y
            lateral = (dx * rx + dy * ry) * side
            along = dx * fx + dy * fy
            # The crossing road starts at the junction entry and spans a few lanes beyond it.
            if lateral < 6.0 or not -conflict.lane_width <= along <= conflict.lane_width * 4.0:
                continue
            score = abs(lateral - 18.0) + abs(along - conflict.lane_width) + (0.0 if item.is_junction else -2.0)
            if best is None or score < best[0]:
                best = (score, item)
        return None if best is None else best[1]

    def ground(self, ir: ScenarioIR, seed: int | None = None, ego_blueprint: str | None = None) -> "GroundedScenario":
        ir = ir.model_copy(update={"map_name": self.map_name})
        ego = self.select_ego(ir, seed)
        warnings = [f"ego lane: {issue}" for issue in ego.issues]
        ego_path = ego.path
        needed = self._required_ahead(ir)
        if ego.path_length < needed:
            ego_path, _ = self.index.walk(ego.lane, needed)

        spawn = self.catalog.spawn_points[ego.spawn_index]
        ego_pose = PlacedEntity(
            entity_name="hero",
            actor_type="car",
            blueprint=ego_blueprint or self.blueprint("ego"),
            x=spawn.x, y=spawn.y, z=ego.lane.z + VEHICLE_Z_OFFSET_M, yaw=spawn.yaw,
            initial_speed_kmh=ir.ego.initial_speed_kmh,
            relative_position="ego",
            lane=(ego.lane.road_id, ego.lane.lane_id),
            method="spawn_point",
            preview=self._preview(ego.lane, ir.ego.initial_speed_kmh),
        )

        actors: list[PlacedEntity] = []
        for idx, act in enumerate(ir.actors):
            placed = self._place_actor(idx, act, ego, ego_path)
            warnings.extend(f"actor_{idx}: {text}" for text in placed.warnings)
            actors.append(placed)

        picks = sorted({round(i * (len(ego_path) - 1) / 4) for i in range(5)})
        route = [{"x": ego_path[i].x, "y": ego_path[i].y, "yaw": ego_path[i].yaw} for i in picks]
        return GroundedScenario(
            ir=ir,
            catalog=self.catalog,
            ego_spawn_index=ego.spawn_index,
            ego=ego_pose,
            actors=actors,
            route=route,
            warnings=warnings,
        )

    def _place_actor(self, idx: int, act, ego: EgoCandidate, ego_path: list[LaneWaypoint]) -> "PlacedEntity":
        distance = float(act.initial_distance_m)
        pos = RelativePosition(act.relative_position)
        is_walker = act.actor_type == ActorType.PEDESTRIAN
        z_offset = WALKER_Z_OFFSET_M if is_walker else VEHICLE_Z_OFFSET_M
        notes: list[str] = []
        lane: LaneWaypoint | None = None
        method = "lane"

        if pos == RelativePosition.BEHIND_SAME_LANE:
            back, travelled = self.index.walk(ego.lane, distance, direction=-1)
            lane = back[-1]
            if travelled < distance - 2.0:
                notes.append(f"lane behind ego ends after {travelled:.0f} m")
            x, y, yaw = lane.x, lane.y, lane.yaw
        else:
            anchor, arc = self._point_at(ego_path, distance)
            if arc < distance - 2.0:
                notes.append(f"ego lane ends after {arc:.0f} m; actor placed at the end")
            if pos == RelativePosition.AHEAD_SAME_LANE:
                lane = anchor
                x, y, yaw = lane.x, lane.y, lane.yaw
            elif pos in (RelativePosition.AHEAD_ADJACENT_LEFT, RelativePosition.AHEAD_ADJACENT_RIGHT):
                side = -1 if pos == RelativePosition.AHEAD_ADJACENT_LEFT else 1
                lane = self.index.neighbor(anchor, side, opposite=False)
                if lane is None:
                    rx, ry = right_of(anchor.yaw)
                    x, y, yaw = anchor.x + rx * side * anchor.lane_width, anchor.y + ry * side * anchor.lane_width, anchor.yaw
                    method = "geometric"
                    notes.append("no adjacent lane at this point; offset by one lane width")
                else:
                    x, y, yaw = lane.x, lane.y, lane.yaw
            elif pos == RelativePosition.ONCOMING:
                lane = self.index.neighbor(anchor, -1, opposite=True)
                if lane is None:
                    rx, ry = right_of(anchor.yaw)
                    x, y, yaw = anchor.x - rx * anchor.lane_width, anchor.y - ry * anchor.lane_width, _wrap(anchor.yaw + 180.0)
                    method = "geometric"
                    notes.append("no oncoming lane at this point; mirrored one lane to the left")
                else:
                    x, y, yaw = lane.x, lane.y, lane.yaw
            else:  # crossing from left/right
                side = -1 if pos == RelativePosition.CROSSING_FROM_LEFT else 1
                # Moving across the ego path: from the left means heading to the ego's right (+90).
                heading = _wrap(anchor.yaw + (90.0 if side < 0 else -90.0))
                if is_walker or act.actor_type == ActorType.BICYCLE:
                    rx, ry = right_of(anchor.yaw)
                    offset = self._edge_offset(anchor, side)
                    x, y, yaw = anchor.x + rx * side * offset, anchor.y + ry * side * offset, heading
                    method = "roadside"
                else:
                    lane = self._crossing_lane(anchor, side)
                    if lane is None:
                        rx, ry = right_of(anchor.yaw)
                        x, y, yaw = anchor.x + rx * side * 15.0, anchor.y + ry * side * 15.0, heading
                        method = "geometric"
                        notes.append("no crossing lane near the conflict point; placed 15 m to the side")
                    else:
                        x, y, yaw = lane.x, lane.y, lane.yaw

        ground_z = lane.z if lane is not None else ego.lane.z
        return PlacedEntity(
            entity_name=f"adversary_{idx}",
            actor_type=act.actor_type.value,
            blueprint=self.blueprint(act.actor_type.value),
            x=x, y=y, z=ground_z + z_offset, yaw=yaw,
            initial_speed_kmh=act.initial_speed_kmh,
            relative_position=pos.value,
            trigger=act.trigger.value if act.trigger else None,
            trigger_distance_m=act.trigger_distance_m,
            lane=(lane.road_id, lane.lane_id) if lane is not None and method == "lane" else None,
            method=method,
            warnings=notes,
            preview=self._preview(lane, act.initial_speed_kmh) if lane is not None and method == "lane"
            else self._straight_preview(x, y, yaw, act.initial_speed_kmh, act.trigger),
        )

    def _preview(self, start: LaneWaypoint, speed_kmh: float, steps: int = 10, dt: float = 0.5) -> list[dict[str, float]]:
        spacing = max(0.5, speed_kmh / 3.6 * dt)
        path, _ = self.index.walk(start, spacing * steps)
        points, arc, target = [], 0.0, 0.0
        previous = path[0]
        points.append({"step": 0, "x": round(previous.x, 2), "y": round(previous.y, 2)})
        for item in path[1:]:
            arc += math.hypot(item.x - previous.x, item.y - previous.y)
            previous = item
            if arc >= target + spacing and len(points) < steps:
                target = arc
                points.append({"step": len(points), "x": round(item.x, 2), "y": round(item.y, 2)})
        return points

    @staticmethod
    def _straight_preview(x: float, y: float, yaw: float, speed_kmh: float, trigger, steps: int = 10, dt: float = 0.5) -> list[dict[str, float]]:
        fx, fy = forward(yaw)
        v = speed_kmh / 3.6
        return [{"step": s, "x": round(x + fx * v * s * dt, 2), "y": round(y + fy * v * s * dt, 2)} for s in range(steps)]


@dataclass
class PlacedEntity:
    entity_name: str
    actor_type: str
    blueprint: str
    x: float
    y: float
    z: float
    yaw: float
    initial_speed_kmh: float
    relative_position: str
    trigger: str | None = None
    trigger_distance_m: float | None = None
    lane: tuple[int, int] | None = None
    method: str = "lane"
    warnings: list[str] = field(default_factory=list)
    preview: list[dict[str, float]] = field(default_factory=list)

    @property
    def initial_speed_ms(self) -> float:
        return self.initial_speed_kmh / 3.6

    @property
    def is_walker(self) -> bool:
        return self.actor_type == ActorType.PEDESTRIAN.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_name": self.entity_name,
            "actor_type": self.actor_type,
            "blueprint": self.blueprint,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
            "yaw_deg": round(self.yaw, 2),
            "initial_speed_kmh": round(self.initial_speed_kmh, 2),
            "relative_position": self.relative_position,
            "trigger": self.trigger,
            "trigger_distance_m": self.trigger_distance_m,
            "lane": None if self.lane is None else {"road_id": self.lane[0], "lane_id": self.lane[1]},
            "placement": self.method,
            "preview_waypoints": self.preview,
        }


@dataclass
class GroundedScenario:
    ir: ScenarioIR
    catalog: CatalogV1
    ego_spawn_index: int
    ego: PlacedEntity
    actors: list[PlacedEntity]
    route: list[dict[str, float]]
    warnings: list[str]

    @property
    def fully_on_lanes(self) -> bool:
        return all(actor.method in {"lane", "roadside"} for actor in self.actors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_name": self.ir.map_name,
            "carla_version": self.catalog.carla_version,
            "catalog_content_hash": self.catalog.content_hash,
            "ego_spawn_index": self.ego_spawn_index,
            "fully_on_lanes": self.fully_on_lanes,
            "ego": self.ego.to_dict(),
            "actors": [actor.to_dict() for actor in self.actors],
            "warnings": self.warnings,
        }
