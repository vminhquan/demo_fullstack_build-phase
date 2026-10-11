"""Kinematic simulation of a composed scenario, assuming the ego does not react.

The ego keeps its lane and speed; every other actor follows its own lane and its timed actions (speed changes at a
rate, lane changes moving linearly to the neighbouring lane). Each step measures every actor against the ego's way:
whether it is in the ego's path, the gap along the path, time-to-collision (TTC) and collisions. "The incident
happens" is then a plain question on these numbers, for any kind of actor or manoeuvre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.scenario.lanes import LaneGraph, Path
from app.scenario.physics import LIMITS
from app.scenario.spec import ActionSpec, ResolvedActor

DT_S = 0.1
PATH_MARGIN_M = 0.1  # extra lateral clearance still counted as "in the ego's path"


class SimulationError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        self.code, self.field = code, field
        super().__init__(message)


@dataclass
class Body:
    actor: ResolvedActor
    path: Path
    d: float  # distance along `path`
    v: float  # m/s along the lane
    target_v: float | None = None
    rate: float = 0.0
    # Lane change in progress: (target path, its distance at start, start time, duration, distance at start).
    change: tuple[Path, float, float, float, float] | None = None

    def position(self, t: float) -> tuple[float, float]:
        if self.change is None:
            return self.path.at(self.d)
        target, start_on_target, t0, duration, start_d = self.change
        w = min(1.0, (t - t0) / duration)
        ax, ay = self.path.at(self.d)
        bx, by = target.at(start_on_target + self.d - start_d)
        return ax + (bx - ax) * w, ay + (by - ay) * w

    def advance(self, t: float) -> None:
        if self.target_v is not None:
            step = self.rate * DT_S
            self.v = self.target_v if abs(self.target_v - self.v) <= step else self.v + math.copysign(step, self.target_v - self.v)
            if self.v == self.target_v:
                self.target_v = None
        self.d += self.v * DT_S
        if self.change is not None:
            target, start_on_target, t0, duration, start_d = self.change
            if t - t0 >= duration:
                self.path, self.d, self.change = target, start_on_target + self.d - start_d, None


@dataclass
class Interaction:
    """One actor measured against the ego over the whole run."""

    in_path_first_s: float | None = None
    entry_ttc_s: float | None = None  # first finite TTC once in the ego's path
    min_ttc_s: float = math.inf
    min_ttc_at_s: float | None = None
    gap_at_min_m: float | None = None
    closing_at_min_kmh: float | None = None
    distance_at_min_m: float | None = None
    collision_at_s: float | None = None
    min_lateral_m: float = math.inf
    ahead_at_entry: bool | None = None  # in front of the ego when it first entered the ego's path
    # At that moment: centre-to-centre distance along the ego's way (+ ahead), both speeds along it (m/s).
    entry_offset_m: float | None = None
    entry_actor_mps: float | None = None
    entry_ego_mps: float | None = None
    entry_gap_m: float | None = None  # bumper-to-bumper gap along the ego's way when it first entered the path
    # At the min TTC: whether the actor was in front of the ego, and how far the ego had driven from its start.
    ahead_at_min: bool | None = None
    ego_travel_at_min_m: float | None = None

    def as_metrics(self) -> dict[str, float | bool | None]:
        finite = math.isfinite(self.min_ttc_s)
        return {
            "min_ttc_s": round(self.min_ttc_s, 2) if finite else None,
            "min_ttc_at_s": self.min_ttc_at_s, "gap_at_min_m": self.gap_at_min_m,
            "closing_at_min_kmh": self.closing_at_min_kmh, "distance_at_min_m": self.distance_at_min_m,
            "entry_ttc_s": self.entry_ttc_s, "in_path_first_s": self.in_path_first_s,
            "collision_at_s": self.collision_at_s, "collision": self.collision_at_s is not None,
        }


@dataclass
class Outcome:
    interactions: dict[str, Interaction]
    speed_at_action: list[float]  # km/h of the actor when each action starts, in action order
    direction_at_action: list[str | None]  # side each lane change went, in action order
    ego_path_length_m: float
    initial_overlaps: list[tuple[str, str]] = field(default_factory=list)


def _into_ego_lane(body: Body, graph: LaneGraph, ego_path: Path, t: float) -> str:
    """The side of the actor on which the ego's lane is, where it is now."""
    here = body.path.point_at(body.d)
    if ego_path.project(here.x, here.y)[1] < 1.0:
        raise SimulationError("ALREADY_IN_EGO_LANE", f"{body.actor.id} lúc {t:g} s đã ở trong làn ego: không cần đổi làn vào.",
                              "actions")
    for side in ("left", "right"):
        lane = graph.neighbor(here, side)
        if lane is not None and ego_path.project(lane.x, lane.y)[1] < 1.0:
            return side
    raise SimulationError("EGO_LANE_NOT_ADJACENT", f"{body.actor.id} lúc {t:g} s không ở làn sát làn ego nên không đổi thẳng "
                          "vào làn ego được: đặt lane_offset là ±1.", "actions")


def _start_action(body: Body, action: ActionSpec, t: float, graph: LaneGraph, ego_path: Path) -> str | None:
    """Start `action`; returns the side of a lane change."""
    limits = LIMITS[body.actor.kind]
    if action.type == "lane_change":
        here = body.path.point_at(body.d)
        side = _into_ego_lane(body, graph, ego_path, t) if action.direction == "into_ego_lane" else action.direction or "left"
        target = graph.neighbor(here, side)
        if target is None:
            raise SimulationError("NO_TARGET_LANE", f"{body.actor.id} lúc {t:g} s không có làn cùng chiều bên "
                                  f"{'trái' if side == 'left' else 'phải'} của nó để đổi sang.", "actions")
        path = graph.path(target.road_id, target.lane_id, target.s, ahead=body.path.length - body.d + 50)
        if path is None:
            raise SimulationError("NO_TARGET_LANE", f"Không dựng được làn đích cho {body.actor.id}.", "actions")
        body.change = (path, 0.0, t, float(action.duration_s or 0.0), body.d)
        return side
    target = 0.0 if action.type == "brake_to_stop" else float(action.target_speed_kmh or 0.0) / 3.6
    default = limits.max_decel_mps2 if target < body.v else limits.max_accel_mps2
    body.target_v, body.rate = target, abs(float(action.acceleration_mps2 or default))
    return None


def simulate(ego: ResolvedActor, actors: list[ResolvedActor], actions: list[ActionSpec], duration_s: float,
             graph: LaneGraph) -> Outcome:
    # The ego's way reaches behind it (actors starting behind) and far ahead of it (actors placed far ahead are still
    # measured); it is cut short only where the road really ends.
    travel = max(ego.speed_kmh, *(actor.speed_kmh for actor in actors), 0.0) / 3.6 * duration_s
    ego_path = graph.path(ego.road_id, ego.lane_id, ego.s, ahead=travel + 400.0, behind=80.0)
    if ego_path is None:
        raise SimulationError("EGO_LANE_NOT_FOUND", "Không tìm thấy làn xe chạy của ego tại vị trí đã chọn.", "ego.start")
    bodies = {"ego": Body(ego, ego_path, ego_path.origin, ego.speed_kmh / 3.6)}
    for actor in actors:
        path = graph.path(actor.road_id, actor.lane_id, actor.s, ahead=travel + 120.0)
        if path is None:
            raise SimulationError("ACTOR_LANE_NOT_FOUND", f"Không tìm thấy làn của {actor.id}.", "actors")
        bodies[actor.id] = Body(actor, path, 0.0, actor.speed_kmh / 3.6)

    outcome = Outcome({actor.id: Interaction() for actor in actors}, [0.0] * len(actions), [None] * len(actions),
                      ego_path.length)
    names = list(bodies)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if _overlap(bodies[a], bodies[b], ego_path):
                outcome.initial_overlaps.append((a, b))

    previous_along: dict[str, float] = {}
    pending = sorted(enumerate(actions), key=lambda item: item[1].at_time_s)
    steps = int(round(duration_s / DT_S))
    for step in range(steps + 1):
        t = round(step * DT_S, 3)
        while pending and pending[0][1].at_time_s <= t + 1e-9:
            index, action = pending.pop(0)
            body = bodies[action.actor]
            outcome.speed_at_action[index] = round(body.v * 3.6, 2)
            outcome.direction_at_action[index] = _start_action(body, action, t, graph, ego_path)
        ego_body = bodies["ego"]
        if ego_body.d > ego_path.length:
            raise SimulationError("EGO_RUNS_OUT_OF_ROAD", f"Ego đi hết đoạn đường đã biết ({ego_path.length - ego_path.origin:.0f} m "
                                  f"phía trước) lúc {t:g} s: giảm tốc độ/thời lượng hoặc chọn vị trí khác.", "ego.start")
        ex, ey = ego_body.position(t)
        for actor in actors:
            body = bodies[actor.id]
            if body.d > body.path.length:
                raise SimulationError("ACTOR_RUNS_OUT_OF_ROAD", f"{actor.id} đi hết đoạn đường đã biết lúc {t:g} s.", "actors")
            _measure(outcome.interactions[actor.id], body, ego_body, ego_path, t, ex, ey, previous_along)
        for body in bodies.values():
            body.advance(t)
    return outcome


def _overlap(a: Body, b: Body, ego_path: Path) -> bool:
    ax, ay = a.position(0.0)
    bx, by = b.position(0.0)
    along_a, _ = ego_path.project(ax, ay)
    along_b, _ = ego_path.project(bx, by)
    along = abs(along_a - along_b)
    lateral = math.sqrt(max(0.0, math.hypot(ax - bx, ay - by) ** 2 - along ** 2))
    return along < (a.actor.length_m + b.actor.length_m) / 2 + 0.5 and lateral < (a.actor.width_m + b.actor.width_m) / 2


def _measure(record: Interaction, body: Body, ego: Body, ego_path: Path, t: float, ex: float, ey: float,
             previous_along: dict[str, float]) -> None:
    x, y = body.position(t)
    along, lateral = ego_path.project(x, y)
    actor, me = body.actor, ego.actor
    record.min_lateral_m = min(record.min_lateral_m, lateral)
    v_along = (along - previous_along[actor.id]) / DT_S if actor.id in previous_along else body.v
    previous_along[actor.id] = along
    in_range = 0.5 < along < ego_path.length - 0.5
    in_path = in_range and lateral < (me.width_m + actor.width_m) / 2 + PATH_MARGIN_M
    if not in_path:
        return
    if along >= ego.d:
        gap, closing = along - actor.length_m / 2 - (ego.d + me.length_m / 2), ego.v - v_along
    else:
        gap, closing = ego.d - me.length_m / 2 - (along + actor.length_m / 2), v_along - ego.v
    if record.in_path_first_s is None:
        record.in_path_first_s, record.ahead_at_entry = t, along >= ego.d
        record.entry_offset_m, record.entry_actor_mps, record.entry_ego_mps = along - ego.d, v_along, ego.v
        record.entry_gap_m = round(gap, 1)
    if gap <= 0:
        if record.collision_at_s is None:
            record.collision_at_s = t
        return
    ttc = gap / closing if closing > 0.05 else math.inf
    if math.isfinite(ttc) and record.entry_ttc_s is None:
        record.entry_ttc_s = round(ttc, 2)
    if ttc < record.min_ttc_s:
        record.min_ttc_s, record.min_ttc_at_s = ttc, t
        record.gap_at_min_m, record.closing_at_min_kmh = round(gap, 1), round(closing * 3.6, 1)
        record.distance_at_min_m = round(math.hypot(x - ex, y - ey), 1)
        record.ahead_at_min, record.ego_travel_at_min_m = along >= ego.d, round(ego.d - ego_path.origin, 1)
