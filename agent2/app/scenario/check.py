"""Turn a composed ScenarioSpec into a checked ScenarioPlan, or the issues to send back to the LLM.

Every check is generic: facts exist in the catalog, actors sit on real lanes, nothing exceeds what its kind can do,
and the simulation (non-reacting ego) shows the incident the scenario is about really happens, in view and in time.
Messages carry the numbers the LLM needs to correct itself.
"""

from __future__ import annotations

import re
import math

from app.catalog.models import SelectedSnapshot
from app.contracts import GenerationRequest
from app.scenario.environment import environment, preset
from app.scenario.lanes import LaneGraph
from app.scenario.physics import LIMITS, kind_of, size
from app.scenario.simulate import SimulationError, simulate
from app.scenario.spec import (
    ActionSpec, ResolvedAction, ResolvedActor, ScenarioPlan, ScenarioSpec, ValidationIssue, ValidationResult,
)

MIN_DURATION_S, MAX_DURATION_S = 5.0, 30.0
LANE_WIDTH_M = 3.5
# The incident: the critical actor in the ego's path with TTC at most this, seen within this distance, and early
# enough to watch the outcome. Entry TTC below the floor leaves no chance to react (unless a collision is the point).
TTC_CRITICAL_S = 3.0
MIN_ENTRY_TTC_S = 0.5
MAX_VIEW_M = 60.0
MIN_OBSERVE_S = 1.0
# Other actors must not cause their own incident.
SIDE_TTC_S = 1.0
ACTOR_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

# Words of the prompt that must show in the scenario. Vocabulary, not scenarios: the kind of a road user, a weather.
KIND_WORDS = {
    "motorcycle": re.compile(r"xe máy|mô ?tô|xe gắn máy"),
    "truck": re.compile(r"xe tải|xe container|xe công"),
    "bus": re.compile(r"xe buýt|xe bus|xe khách"),
    "bicycle": re.compile(r"xe đạp"),
    "van": re.compile(r"xe van|xe bán tải"),
}
WEATHER_WORDS = {
    "rain": re.compile(r"mưa"), "fog": re.compile(r"sương mù"), "cloudy": re.compile(r"nhiều mây|âm u"),
    "sunny": re.compile(r"nắng|trời quang"), "wet": re.compile(r"đường ướt|mặt đường ướt"), "dust": re.compile(r"bão bụi|bụi mù|bụi"),
}
LIGHT_WORDS = {"night": re.compile(r"ban đêm|buổi tối|trời tối|\bđêm\b"), "sunset": re.compile(r"hoàng hôn|chiều tối"),
               "day": re.compile(r"ban ngày|buổi trưa|buổi sáng")}
ROAD_WORDS = {"curve": re.compile(r"khúc cua|đường cong|cua gấp|đường vòng"),
              "junction": re.compile(r"giao lộ|ngã tư|ngã ba|nút giao"), "four_way": re.compile(r"ngã tư")}
# What the critical actor must do when the prompt says so. Vocabulary mapped to a checkable behaviour.
CUT_IN_WORDS = re.compile(r"tạt đầu|cắt đầu|cắt ngang đầu|chen ngang|chen vào|lấn làn|cut-?in")
BRAKE_WORDS = re.compile(r"phanh gấp|phanh đột ngột|dừng đột ngột|dừng gấp")
REAR_WORDS = re.compile(r"phía sau|từ sau|đâm đuôi|tông đuôi|húc đuôi|đâm từ sau")
# A cut-in merges close in front of the ego: bumper gap at the merge at most this time at the ego's speed (never below
# the floor). Further ahead it looks like an early, harmless lane change.
CUT_IN_HEADWAY_S = 1.0
CUT_IN_MIN_GAP_M = 6.0
# The incident counts as "at the junction" from this far before its first waypoint to this far inside it.
JUNCTION_BEFORE_M, JUNCTION_INSIDE_M = 40.0, 15.0


def _issue(code: str, message: str, field: str | None = None, fix=None) -> ValidationIssue:
    """`fix`: (changed spec, note) when only numbers are wrong and simulation found ones that pass."""
    spec, note = fix if fix else (None, None)
    return ValidationIssue(code=code, message=message, field=field, fix=spec, fix_note=note)


def _words(text: str) -> str:
    return " ".join(text.lower().split())


def lanes_around(graph: LaneGraph, road_id: int, lane_id: int, s: float, *, subject: str = "Ego") -> str:
    """Same-direction lane offsets at a precise point, rather than elsewhere along the ego's path."""
    found = graph.locate(road_id, lane_id, s)
    if found is None:
        return ""
    offsets = []
    for side, sign, name in (("left", 1, "trái"), ("right", -1, "phải")):
        point, step = found.point, 0
        while (point := graph.neighbor(point, side)) is not None and step < 6:
            step += 1
            offsets.append(f"{sign * step:+d} ({name})")
    if not offsets:
        return f"{subject} đang ở đường chỉ có 1 làn cùng chiều: chỉ dùng lane_offset 0, hoặc chọn vị trí khác."
    return f"Làn cùng chiều quanh {subject.lower()}: lane_offset 0 (làn ego), {', '.join(offsets)}; không có làn nào khác."


def check(spec: ScenarioSpec, request: GenerationRequest, snapshots: list[SelectedSnapshot],
          graphs: dict[str, LaneGraph]) -> ScenarioPlan | ValidationResult:
    snapshot = next((item for item in snapshots if item.ref.map_name == spec.map_name), None)
    if snapshot is None:
        return ValidationResult(valid=False, issues=[_issue("MAP_NOT_SELECTED", "Map không nằm trong các map đã chọn.", "map_name")])
    issues = _facts(spec, request, snapshot)
    if issues:
        return ValidationResult(valid=False, issues=issues)
    graph = graphs[spec.map_name]
    placed = _place(spec, request, snapshot, graph)
    if isinstance(placed, ValidationResult):
        return placed
    ego, actors = placed
    issues = _physics(spec, actors)
    if issues:
        return ValidationResult(valid=False, issues=issues)
    try:
        outcome = simulate(ego, actors, spec.actions, spec.duration_s, graph)
    except SimulationError as exc:
        message = str(exc)
        if exc.code in ("NO_TARGET_LANE", "EGO_LANE_NOT_ADJACENT", "ALREADY_IN_EGO_LANE"):
            message += " " + lanes_around(graph, ego.road_id, ego.lane_id, ego.s) + " Dùng direction=into_ego_lane để đổi vào làn ego."
        return ValidationResult(valid=False, issues=[_issue(exc.code, message, exc.field)])
    issues = [_issue("INITIAL_OVERLAP", f"{a} và {b} chồng lên nhau lúc bắt đầu: tách xa hơn (ds_m/lane_offset).", "actors")
              for a, b in outcome.initial_overlaps]
    cut_in = bool(CUT_IN_WORDS.search(_words(request.prompt)))
    issues += _incident(spec, outcome, probe=lambda changed: _passes(changed, request, snapshot, graph, cut_in), cut_in=cut_in)
    issues += _prompt(spec, request, snapshot, actors, graph, ego, outcome)
    if issues:
        return ValidationResult(valid=False, issues=issues)
    return ScenarioPlan(
        title=spec.title, ego=ego, actors=actors,
        actions=[ResolvedAction(**action.model_dump(), speed_at_start_kmh=speed, resolved_direction=side)
                 for action, speed, side in zip(spec.actions, outcome.speed_at_action, outcome.direction_at_action, strict=True)],
        critical_actor=spec.critical_actor, expected=spec.expected, duration_s=spec.duration_s,
        environment=environment(spec.weather_preset, spec.road_surface, spec.lighting),
        metrics=outcome.interactions[spec.critical_actor].as_metrics(),
    )


def _facts(spec: ScenarioSpec, request: GenerationRequest, snapshot: SelectedSnapshot) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    catalog = snapshot.catalog
    vehicles = {item.id: item for item in catalog.vehicles}
    if request.ego_blueprint_id and spec.ego.blueprint_id not in (None, request.ego_blueprint_id):
        issues.append(_issue("EGO_LOCKED", f"Ô tô ego đã khóa là {request.ego_blueprint_id}; để ego.blueprint_id null.", "ego.blueprint_id"))
    ego = vehicles.get(request.ego_blueprint_id or spec.ego.blueprint_id or "")
    if ego is None or kind_of(ego) != "car":
        issues.append(_issue("EGO_NOT_CAR", "Ô tô ego không có trong catalog hoặc không phải ô tô.", "ego.blueprint_id"))
    ids = [actor.id for actor in spec.actors]
    if not spec.actors:
        issues.append(_issue("NO_ACTOR", "Cần ít nhất một tác nhân gây ra sự cố.", "actors"))
    if len(set(ids)) != len(ids) or any(not ACTOR_ID.match(item) or item.lower() in ("ego", "hero") for item in ids):
        issues.append(_issue("BAD_ACTOR_ID", "id tác nhân phải duy nhất, dạng chữ/số/gạch dưới, khác 'ego'/'hero'.", "actors"))
    for actor in spec.actors:
        blueprint = vehicles.get(actor.blueprint_id)
        if blueprint is None or kind_of(blueprint) is None:
            issues.append(_issue("UNKNOWN_BLUEPRINT", f"{actor.id}: blueprint {actor.blueprint_id} không có trong catalog.vehicles.",
                                 "actors"))
    if spec.critical_actor not in ids:
        issues.append(_issue("CRITICAL_NOT_AN_ACTOR", "critical_actor phải là id của một tác nhân.", "critical_actor"))
    for action in spec.actions:
        if action.actor not in ids:
            issues.append(_issue("ACTION_UNKNOWN_ACTOR", f"Hành động cho '{action.actor}' không phải tác nhân.", "actions"))
        elif action.type == "lane_change" and (action.direction is None or not action.duration_s):
            issues.append(_issue("LANE_CHANGE_INCOMPLETE", "lane_change cần direction và duration_s.", "actions"))
        elif action.type == "speed" and action.target_speed_kmh is None:
            issues.append(_issue("SPEED_INCOMPLETE", "speed cần target_speed_kmh.", "actions"))
        if not 0 <= action.at_time_s < spec.duration_s:
            issues.append(_issue("ACTION_OUT_OF_TIME", f"at_time_s của {action.actor} phải trong [0, duration_s).", "actions"))
    if spec.weather_preset not in catalog.weather_presets or preset(spec.weather_preset) is None:
        issues.append(_issue("WEATHER_NOT_AVAILABLE", "Weather preset không có hoặc không dùng được; chọn từ catalog.weather_presets.",
                             "weather_preset"))
    elif spec.road_surface == "dry" and preset(spec.weather_preset).conditions & {"rain", "wet"}:
        issues.append(_issue("ENVIRONMENT_CONFLICT", "Mặt đường khô mâu thuẫn với preset mưa/ướt.", "road_surface"))
    if not MIN_DURATION_S <= spec.duration_s <= MAX_DURATION_S:
        issues.append(_issue("BAD_DURATION", f"duration_s phải trong [{MIN_DURATION_S:g}, {MAX_DURATION_S:g}].", "duration_s"))
    return issues


def _place(spec: ScenarioSpec, request: GenerationRequest, snapshot: SelectedSnapshot,
           graph: LaneGraph) -> tuple[ResolvedActor, list[ResolvedActor]] | ValidationResult:
    vehicles = {item.id: item for item in snapshot.catalog.vehicles}
    start = graph.locate(spec.ego.start.road_id, spec.ego.start.lane_id, spec.ego.start.s)
    if start is None:
        return ValidationResult(valid=False, issues=[_issue(
            "EGO_LANE_NOT_FOUND", "Không có làn xe chạy tại ego.start; lấy vị trí từ find_ego_starts hoặc search_cut_in_sites.",
            "ego.start")])
    ego_blueprint = vehicles[request.ego_blueprint_id or spec.ego.blueprint_id]
    length, width, height = size(ego_blueprint, "car")
    ego = ResolvedActor(id="hero", blueprint_id=ego_blueprint.id, kind="car", length_m=length, width_m=width, height_m=height,
                        road_id=start.point.road_id, lane_id=start.point.lane_id, s=start.point.s, speed_kmh=spec.ego.speed_kmh)
    behind = max([0.0, *(-actor.ds_m for actor in spec.actors)]) + 10.0
    way = graph.path(start.point.road_id, start.point.lane_id, start.point.s,
                     ahead=max([0.0, *(actor.ds_m for actor in spec.actors)]) + 10.0, behind=behind)
    actors, issues = [], []
    for actor in spec.actors:
        along = way.origin + actor.ds_m
        if not 0 <= along <= way.length:
            issues.append(_issue("ACTOR_OFF_ROAD", f"{actor.id}: ds_m={actor.ds_m:g} vượt ra ngoài đoạn đường liền mạch của ego "
                                 f"(từ -{way.origin:.0f} đến +{way.length - way.origin:.0f} m).", "actors"))
            continue
        base = way.point_at(along)
        point = graph.offset_lane(base, actor.lane_offset)
        if point is None:
            mirrored = graph.offset_lane(base, -actor.lane_offset) if actor.lane_offset else None
            issues.append(_issue("NO_SUCH_LANE", f"{actor.id}: không có làn cùng chiều ở lane_offset={actor.lane_offset}. "
                                 + lanes_around(graph, base.road_id, base.lane_id, base.s,
                                                subject=f"vị trí {actor.id} (ds_m={actor.ds_m:g})"), "actors",
                                 _mirror(spec, actor.id) if mirrored is not None else None))
            continue
        blueprint = vehicles[actor.blueprint_id]
        kind = kind_of(blueprint)
        length, width, height = size(blueprint, kind)
        actors.append(ResolvedActor(id=actor.id, blueprint_id=blueprint.id, kind=kind, length_m=length, width_m=width,
                                    height_m=height, road_id=point.road_id, lane_id=point.lane_id, s=point.s,
                                    speed_kmh=actor.speed_kmh))
    if issues:
        return ValidationResult(valid=False, issues=issues)
    return ego, actors


def _physics(spec: ScenarioSpec, actors: list[ResolvedActor]) -> list[ValidationIssue]:
    kinds = {actor.id: actor.kind for actor in actors}
    issues: list[ValidationIssue] = []
    if not 0 < spec.ego.speed_kmh <= LIMITS["car"].max_speed_kmh:
        issues.append(_issue("SPEED_LIMIT", f"Tốc độ ego phải trong (0, {LIMITS['car'].max_speed_kmh:g}] km/h.", "ego.speed_kmh"))
    for actor in spec.actors:
        limits = LIMITS[kinds[actor.id]]
        if not 0 <= actor.speed_kmh <= limits.max_speed_kmh:
            issues.append(_issue("SPEED_LIMIT", f"{actor.id} ({kinds[actor.id]}) chạy tối đa {limits.max_speed_kmh:g} km/h.", "actors"))
    for action in spec.actions:
        limits = LIMITS[kinds[action.actor]]
        if action.type == "speed" and not 0 <= (action.target_speed_kmh or 0) <= limits.max_speed_kmh:
            issues.append(_issue("SPEED_LIMIT", f"{action.actor}: target_speed_kmh tối đa {limits.max_speed_kmh:g}.", "actions"))
        if action.acceleration_mps2 is not None and action.type != "lane_change":
            top = limits.max_decel_mps2 if action.type == "brake_to_stop" else max(limits.max_accel_mps2, limits.max_decel_mps2)
            if not 0 < abs(action.acceleration_mps2) <= top:
                issues.append(_issue("ACCELERATION_LIMIT", f"{action.actor} ({kinds[action.actor]}): gia/giảm tốc tối đa {top:g} m/s².",
                                     "actions"))
        if action.type == "lane_change" and action.duration_s:
            shortest = LANE_WIDTH_M / limits.max_lateral_mps
            if action.duration_s < shortest:
                duration = math.ceil(shortest * 10 - 1e-9) / 10
                actions = [item.model_copy(update={"duration_s": duration}) if item is action else item
                           for item in spec.actions]
                changed = spec.model_copy(update={"actions": actions})
                issues.append(_issue("LANE_CHANGE_TOO_FAST", f"{action.actor} ({kinds[action.actor]}) cần ít nhất {shortest:.1f} s "
                                     "để đổi một làn.", "actions", (changed, f"{action.actor} đổi làn trong {duration:g} s")))
    return issues


def _passes(spec: ScenarioSpec, request: GenerationRequest, snapshot: SelectedSnapshot, graph: LaneGraph, cut_in: bool) -> bool:
    """Would the incident check pass for this changed spec? Used to turn a failure into numbers that work."""
    placed = _place(spec, request, snapshot, graph)
    if isinstance(placed, ValidationResult) or _physics(spec, placed[1]):
        return False
    try:
        outcome = simulate(placed[0], placed[1], spec.actions, spec.duration_s, graph)
    except SimulationError:
        return False
    return not _incident(spec, outcome, cut_in=cut_in)


def _incident(spec: ScenarioSpec, outcome, probe=None, cut_in: bool = False) -> list[ValidationIssue]:
    """The incident must happen: the critical actor in the ego's path, close in time and space, before the end."""
    record = outcome.interactions[spec.critical_actor]
    latest = spec.duration_s - MIN_OBSERVE_S
    name = spec.critical_actor
    issues: list[ValidationIssue] = []
    if record.in_path_first_s is None:
        changes = any(action.actor == name and action.type == "lane_change" for action in spec.actions)
        hint = ("thêm hành động lane_change direction=into_ego_lane cho tác nhân này" if not changes
                else "lần đổi làn chưa đưa tác nhân vào làn ego: dùng direction=into_ego_lane")
        issues.append(_issue("NO_INCIDENT", f"{name} không bao giờ đi vào đường của ego (lệch ngang nhỏ nhất "
                             f"{record.min_lateral_m:.1f} m): {hint}, hoặc xuất phát trong làn ego (lane_offset 0).", "actions"))
    elif spec.expected == "collision":
        if record.collision_at_s is None or record.collision_at_s > latest:
            issues.append(_issue("NO_COLLISION", f"Prompt muốn va chạm nhưng không có va chạm trước {latest:g} s "
                                 f"(TTC nhỏ nhất {_ttc(record)}): đưa {name} gần hơn hoặc tăng chênh lệch tốc độ.", "actions"))
    elif record.collision_at_s is not None and (record.entry_ttc_s is None or record.entry_ttc_s < MIN_ENTRY_TTC_S) \
            or record.entry_ttc_s is not None and record.entry_ttc_s < MIN_ENTRY_TTC_S:
        when = (f"khi TTC chỉ còn {record.entry_ttc_s:g} s" if record.entry_ttc_s is not None
                else f"và va chạm ngay lúc {record.collision_at_s:g} s")
        hint, fix = _closer_hint(spec, probe, adjust_speed=cut_in)
        issues.append(_issue("INCIDENT_UNAVOIDABLE", f"{name} vào đường của ego {when} (< {MIN_ENTRY_TTC_S:g} s): ego không có "
                             "cơ hội xử lý. Tăng khoảng cách lúc vào làn, hoặc expected=collision nếu prompt muốn va chạm. "
                             "Gợi ý: " + hint, "actions", fix))
    elif cut_in and record.ahead_at_entry and record.entry_gap_m is not None \
            and record.entry_gap_m > (limit := max(CUT_IN_MIN_GAP_M, CUT_IN_HEADWAY_S * record.entry_ego_mps)):
        hint, fix = _closer_hint(spec, probe, adjust_speed=True)
        issues.append(_issue("CUT_IN_TOO_FAR", f"{name} vào làn ego cách đầu ego {record.entry_gap_m:.0f} m: tạt đầu phải sát "
                             f"hơn, tối đa {limit:.0f} m ({CUT_IN_HEADWAY_S:g} s theo tốc độ ego). Gợi ý: " + hint, "actions", fix))
    elif record.min_ttc_at_s is None or record.min_ttc_s > TTC_CRITICAL_S or record.min_ttc_at_s > latest:
        fix = None
        if record.min_ttc_at_s is None and record.ahead_at_entry is False:
            detail = f"{name} vào làn ego lúc {record.in_path_first_s:g} s nhưng ở PHÍA SAU ego và không đuổi kịp ego"
            hint = _overtake_hint(spec, record, outcome)
        elif record.min_ttc_at_s is None:
            detail = (f"{name} vào làn ego lúc {record.in_path_first_s:g} s ở phía trước "
                      f"{record.entry_offset_m:.0f} m nhưng chạy {record.entry_actor_mps * 3.6:.0f} km/h, không chậm hơn ego "
                      f"({record.entry_ego_mps * 3.6:.0f} km/h), nên ego không bao giờ tiến lại gần")
            hint, fix = _slow_down_hint(spec, record, probe)
        else:
            gap, closing = record.gap_at_min_m or 0.0, (record.closing_at_min_kmh or 0.0) / 3.6
            detail = (f"TTC nhỏ nhất {record.min_ttc_s:.1f} s lúc {record.min_ttc_at_s:g} s (cách {gap:.0f} m, "
                      f"ego nhanh hơn {closing * 3.6:.0f} km/h)")
            hint = (f"để TTC <= {TTC_CRITICAL_S:g} s cần khoảng cách <= {TTC_CRITICAL_S * closing:.0f} m ở tốc độ đó, "
                    f"hoặc chênh lệch tốc độ >= {gap / TTC_CRITICAL_S * 3.6:.0f} km/h; sự cố phải xảy ra trước {latest:g} s")
        issues.append(_issue("INCIDENT_TOO_MILD", f"Sự cố chưa xảy ra: {detail}. Gợi ý: {hint}.", "actions", fix))
    elif (record.distance_at_min_m or 0) > MAX_VIEW_M:
        issues.append(_issue("INCIDENT_OUT_OF_VIEW", f"Sự cố xảy ra cách ego {record.distance_at_min_m:.0f} m (> {MAX_VIEW_M:g} m).",
                             "actors"))
    for actor, other in outcome.interactions.items():
        if actor != spec.critical_actor and (other.collision_at_s is not None or other.min_ttc_s < SIDE_TTC_S):
            issues.append(_issue("SIDE_INCIDENT", f"{actor} (không phải critical_actor) cũng gây xung đột với ego "
                                 f"(TTC nhỏ nhất {_ttc(other)}): đặt xa hơn.", "actors"))
    return issues


def _slow_down_hint(spec: ScenarioSpec, record, probe) -> tuple[str, tuple | None]:
    """The highest speed the actor can drop to right after merging for the incident to pass, found by simulation."""
    at = round(record.in_path_first_s, 1)
    kind = next(item for item in spec.actors if item.id == spec.critical_actor)
    decel = 4.0
    ego_kmh = int(spec.ego.speed_kmh)
    for target in range(ego_kmh - ego_kmh % 5, 0, -5):
        slow = ActionSpec(actor=kind.id, at_time_s=at, type="speed", target_speed_kmh=float(target),
                          acceleration_mps2=decel, direction=None, duration_s=None)
        changed = spec.model_copy(update={"actions": [*spec.actions, slow]})
        if probe is not None and probe(changed):
            return (f"ngay sau khi đổi làn, thêm hành động speed cho {kind.id}: at_time_s={at:g}, target_speed_kmh={target}, "
                    f"acceleration_mps2={decel:g} (đã thử bằng mô phỏng: đạt). Tác nhân có thể nhanh trước khi đổi làn nhưng "
                    "phải chậm hơn ego sau khi vào làn"), (changed, f"{kind.id} giảm xuống {target} km/h lúc {at:g} s")
    return f"tác nhân vào làn quá xa phía trước ({record.entry_offset_m:.0f} m): giảm ds_m hoặc vào làn sớm hơn", None


MAX_NEARBY_TRIES = 80


def _closer_hint(spec: ScenarioSpec, probe, *, adjust_speed: bool = False) -> tuple[str, tuple | None]:
    """Find nearby placement, then a slower actor if its current speed makes a close merge impossible."""
    actor = next(item for item in spec.actors if item.id == spec.critical_actor)
    change = next((index for index, action in enumerate(spec.actions)
                   if action.actor == actor.id and action.type == "lane_change"), None)
    if probe is None or change is None:
        return "chỉnh ds_m của tác nhân hoặc thời điểm đổi làn.", None
    start_time = spec.actions[change].at_time_s
    delays = (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, 2.0, 3.0)
    # Smallest change first, in both directions (2 m on the start counts like 0.5 s on the lane change); then, for an
    # actor placed far away, starts near the ego.
    nearby = sorted(((abs(step) + abs(delay) * 2, actor.ds_m + step * 2.0, delay)
                     for step in range(-20, 21) for delay in delays if step or delay), key=lambda item: item[0])
    near_ego = sorted(((abs(ds - 10.0) / 2 + abs(delay) * 2, ds, delay)
                       for ds in (float(value) for value in range(-10, 42, 2)) for delay in delays), key=lambda item: item[0])
    tries = [item for item in nearby[:MAX_NEARBY_TRIES] + near_ego[:MAX_NEARBY_TRIES] if start_time + item[2] >= 0]
    for _, ds, delay in tries:
        actions = list(spec.actions)
        actions[change] = actions[change].model_copy(update={"at_time_s": start_time + delay})
        actors = [item.model_copy(update={"ds_m": ds}) if item.id == actor.id else item for item in spec.actors]
        changed = spec.model_copy(update={"actors": actors, "actions": actions})
        if probe(changed):
            when = start_time + delay
            return (f"đặt {actor.id} ds_m={ds:g} và lane_change at_time_s={when:g} (đã thử bằng mô phỏng: đạt).",
                    (changed, f"{actor.id} xuất phát ds_m={ds:g}, đổi làn lúc {when:g} s"))
    if adjust_speed and actor.speed_kmh >= spec.ego.speed_kmh:
        # Moving ds_m alone cannot make a fast actor close in front *and* give the ego a finite TTC. Keep the
        # manoeuvre and search slower starting speeds; a speed action that maintained the old speed follows it.
        targets = {id(action) for action in spec.actions if action.actor == actor.id and action.type == "speed"
                   and action.target_speed_kmh is not None and action.target_speed_kmh >= actor.speed_kmh}
        start_offsets = sorted((float(ds) for ds in range(-10, 42, 2)), key=lambda ds: abs(ds - actor.ds_m))
        for speed in (spec.ego.speed_kmh - 10, spec.ego.speed_kmh - 20, spec.ego.speed_kmh - 30):
            if speed <= 0:
                continue
            for ds in start_offsets:
                actors = [item.model_copy(update={"ds_m": ds, "speed_kmh": speed}) if item.id == actor.id else item
                          for item in spec.actors]
                actions = [item.model_copy(update={"target_speed_kmh": speed}) if id(item) in targets else item
                           for item in spec.actions]
                changed = spec.model_copy(update={"actors": actors, "actions": actions})
                if probe(changed):
                    return (f"đặt {actor.id} ds_m={ds:g}, speed_kmh={speed:g}"
                            " (đã thử bằng mô phỏng: đạt).",
                            (changed, f"{actor.id} xuất phát ds_m={ds:g}, chạy {speed:g} km/h"))
    return "chỉnh ds_m, thời điểm đổi làn và tốc độ tác nhân (mô phỏng chưa tìm được số gần đó đạt).", None


def _mirror(spec: ScenarioSpec, actor_id: str) -> tuple:
    """The actor on the other side of the ego (the side that has a lane), its explicit lane changes mirrored too."""
    swap = {"left": "right", "right": "left"}
    actors = [item.model_copy(update={"lane_offset": -item.lane_offset}) if item.id == actor_id else item for item in spec.actors]
    actions = [action.model_copy(update={"direction": swap.get(action.direction, action.direction)})
               if action.actor == actor_id and action.type == "lane_change" else action for action in spec.actions]
    lane = next(item.lane_offset for item in actors if item.id == actor_id)
    return spec.model_copy(update={"actors": actors, "actions": actions}), f"{actor_id} sang làn phía có thật (lane_offset {lane:+d})"


def _overtake_hint(spec: ScenarioSpec, record, outcome) -> str:
    """Speed (or start) that puts the actor ahead of the ego when it changes lanes."""
    entry = record.in_path_first_s or 1.0
    actor = next(item for item in spec.actors if item.id == spec.critical_actor)
    ego_kmh = spec.ego.speed_kmh
    # Ahead by a few metres when merging: start offset + (v_actor - v_ego) * t >= margin.
    margin = 8.0
    needed = ego_kmh + (margin - actor.ds_m) / entry * 3.6
    return (f"tạt đầu là vào làn ego ở PHÍA TRƯỚC ego: đặt ds_m >= {margin + (ego_kmh - actor.speed_kmh) / 3.6 * entry:.0f} m "
            f"với tốc độ hiện tại, hoặc giữ ds_m={actor.ds_m:g} nhưng tác nhân phải >= {needed:.0f} km/h để vượt lên trước "
            "khi đổi làn; sau khi vào làn thì giảm tốc xuống chậm hơn ego")


def _ttc(record) -> str:
    return f"{record.min_ttc_s:.1f} s" if record.min_ttc_at_s is not None else "không có"


def _prompt(spec: ScenarioSpec, request: GenerationRequest, snapshot: SelectedSnapshot, actors: list[ResolvedActor],
            graph: LaneGraph, ego: ResolvedActor, outcome) -> list[ValidationIssue]:
    """What the prompt (or the form) states must show in the scenario: actors, behaviour, place, weather."""
    text = _words(request.prompt)
    issues: list[ValidationIssue] = []
    critical = next(actor for actor in actors if actor.id == spec.critical_actor)
    record = outcome.interactions[spec.critical_actor]
    kinds = {actor.kind for actor in actors}
    for kind, pattern in KIND_WORDS.items():
        if pattern.search(text) and kind not in kinds:
            issues.append(_issue("PROMPT_ACTOR_MISSING", f"Prompt nhắc '{pattern.search(text).group(0)}' nhưng kịch bản không có "
                                 f"tác nhân loại {kind}.", "actors"))
    if request.actor_type and critical.kind != request.actor_type:
        issues.append(_issue("ACTOR_LOCKED", f"Form đã chọn tác nhân gây sự cố loại {request.actor_type}; "
                             f"critical_actor đang là {critical.kind}.", "critical_actor"))
    issues += _behaviour(spec, text, critical, record)
    found = preset(spec.weather_preset)
    if found is not None:
        for condition, pattern in WEATHER_WORDS.items():
            if pattern.search(text) and condition not in found.conditions:
                issues.append(_issue("PROMPT_WEATHER_MISMATCH", f"Prompt nhắc '{pattern.search(text).group(0)}' nhưng preset "
                                     f"{found.name} không có {condition}.", "weather_preset"))
        lighting = spec.lighting or found.lighting
        for light, pattern in LIGHT_WORDS.items():
            if pattern.search(text) and lighting != light:
                issues.append(_issue("PROMPT_LIGHT_MISMATCH", f"Prompt nhắc '{pattern.search(text).group(0)}' nhưng ánh sáng là "
                                     f"{lighting} (preset {found.name}): đặt lighting='{light}' hoặc chọn preset {light}.",
                                     "lighting"))
    way = graph.path(ego.road_id, ego.lane_id, ego.s, ahead=400.0)
    if ROAD_WORDS["curve"].search(text) and way is not None and way.heading_change(0.0, 120.0) < 15.0:
        issues.append(_issue("PROMPT_ROAD_MISMATCH", "Prompt nói khúc cua nhưng 120 m phía trước ego gần như thẳng "
                             f"({way.heading_change(0.0, 120.0):.0f}°): chọn vị trí có tag curve.", "ego.start"))
    if ROAD_WORDS["junction"].search(text) and way is not None:
        issues += _at_junction(spec, text, graph, way, ego, record)
    return issues


def _behaviour(spec: ScenarioSpec, text: str, critical: ResolvedActor, record) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    own = [action for action in spec.actions if action.actor == critical.id]
    if CUT_IN_WORDS.search(text):
        changes = any(action.type == "lane_change" for action in own)
        if not changes or record.ahead_at_entry is not True or not record.in_path_first_s:
            issues.append(_issue("PROMPT_BEHAVIOUR_MISMATCH", f"Prompt nói '{CUT_IN_WORDS.search(text).group(0)}': "
                                 f"{critical.id} phải xuất phát ở làn bên cạnh (lane_offset ±1), lane_change "
                                 "direction=into_ego_lane và vào làn ego ở PHÍA TRƯỚC ego. Hiện tại: "
                                 + ("không đổi làn" if not changes else "vào làn ego ở phía sau" if record.ahead_at_entry is False
                                    else "đã ở sẵn trong làn ego"), "actions"))
    if BRAKE_WORDS.search(text):
        brakes = any(action.type == "brake_to_stop" or (action.type == "speed" and (action.target_speed_kmh or 0) < critical.speed_kmh)
                     for action in own)
        if not brakes or record.ahead_at_min is False:
            issues.append(_issue("PROMPT_BEHAVIOUR_MISMATCH", f"Prompt nói '{BRAKE_WORDS.search(text).group(0)}': {critical.id} "
                                 "phải ở PHÍA TRƯỚC ego và có hành động brake_to_stop (hoặc giảm tốc mạnh).", "actions"))
    if record.ahead_at_min is False and not REAR_WORDS.search(text):
        issues.append(_issue("REAR_INCIDENT", f"Sự cố xảy ra với {critical.id} ở PHÍA SAU ego (đâm vào đuôi ego) nhưng prompt "
                             "không nói sự cố từ phía sau: đặt tác nhân gây sự cố phía trước ego.", "actors"))
    return issues


def _at_junction(spec: ScenarioSpec, text: str, graph: LaneGraph, way, ego: ResolvedActor, record) -> list[ValidationIssue]:
    ahead = way.junction_ahead(0.0, way.length)
    if ahead is None:
        return [_issue("PROMPT_ROAD_MISMATCH", "Prompt nói giao lộ nhưng đường đi của ego không qua giao lộ nào: dùng "
                       "find_ego_starts tags=['junction_approach'] (ngã tư: 'intersection_4way_approach').", "ego.start")]
    distance, junction_id = ahead
    issues = []
    if ROAD_WORDS["four_way"].search(text) and not graph.is_four_way(junction_id):
        issues.append(_issue("PROMPT_ROAD_MISMATCH", f"Prompt nói ngã tư nhưng giao lộ phía trước ego (cách {distance:.0f} m) không "
                             "phải ngã tư: dùng find_ego_starts tags=['intersection_4way_approach'].", "ego.start"))
    if record.ego_travel_at_min_m is not None:
        front = record.ego_travel_at_min_m + ego.length_m / 2
        if not distance - JUNCTION_BEFORE_M <= front <= distance + JUNCTION_INSIDE_M:
            issues.append(_issue("INCIDENT_NOT_AT_JUNCTION", f"Prompt nói giao lộ nhưng sự cố xảy ra khi ego đã đi {front:.0f} m, "
                                 f"còn giao lộ cách vị trí xuất phát {distance:.0f} m: sự cố phải xảy ra trong khoảng "
                                 f"{max(distance - JUNCTION_BEFORE_M, 0):.0f}-{distance + JUNCTION_INSIDE_M:.0f} m (chỉnh vị trí "
                                 "xuất phát của ego, thời điểm hoặc tốc độ).", "ego.start",
                                 _move_start(spec, graph, ego, (distance - 12.5) - front)))
    return issues


def _move_start(spec: ScenarioSpec, graph: LaneGraph, ego: ResolvedActor, shift: float) -> tuple | None:
    """The spec with the ego (and every actor placed relative to it) starting `shift` metres further along its lane;
    where the road does not go that far, the actions run that much later (shift > 0) or earlier (shift < 0) instead."""
    path = graph.path(ego.road_id, ego.lane_id, ego.s, ahead=max(shift, 0.0) + 1.0, behind=max(-shift, 0.0))
    if path is not None and 0 <= path.origin + shift <= path.length:
        point = path.point_at(path.origin + shift)
        start = spec.ego.start.model_copy(update={"road_id": point.road_id, "lane_id": point.lane_id, "s": point.s})
        changed = spec.model_copy(update={"ego": spec.ego.model_copy(update={"start": start})})
        return changed, f"ego xuất phát dời {shift:+.0f} m (road {point.road_id}, lane {point.lane_id}, s {point.s:g})"
    delay = round(shift / max(spec.ego.speed_kmh / 3.6, 0.1), 1)
    if spec.duration_s + delay > MAX_DURATION_S or any(action.at_time_s + delay < 0 for action in spec.actions):
        return None
    actions = [action.model_copy(update={"at_time_s": round(action.at_time_s + delay, 1)}) for action in spec.actions]
    changed = spec.model_copy(update={"actions": actions, "duration_s": max(MIN_DURATION_S, spec.duration_s + max(delay, 0.0))})
    return changed, f"mọi hành động {'lùi' if delay > 0 else 'sớm'} {abs(delay):g} s để sự cố xảy ra ở giao lộ"


MAX_TUNING_ROUNDS = 4


def tune(spec: ScenarioSpec, request: GenerationRequest, snapshots: list[SelectedSnapshot],
         graphs: dict[str, LaneGraph]) -> ScenarioPlan | ValidationResult:
    """check(), and when every issue is only about numbers that simulation can fix, apply those fixes and check again.

    The LLM decides who does what where; this only moves starts, times and speeds. A plan lists what was tuned; a
    failure keeps the issues of the last spec tried."""
    notes: list[str] = []
    result: ScenarioPlan | ValidationResult = ValidationResult(valid=False)
    for _ in range(MAX_TUNING_ROUNDS + 1):
        result = check(spec, request, snapshots, graphs)
        if isinstance(result, ScenarioPlan):
            return result.model_copy(update={"tuning": notes})
        if not result.issues or any(issue.fix is None for issue in result.issues):
            break
        issue = result.issues[0]
        spec = issue.fix
        notes.append(f"{issue.code}: {issue.fix_note}")
    if notes:
        result.issues.append(_issue("TUNING_TRIED", "Hệ thống đã tự chỉnh số liệu: " + "; ".join(notes)
                                    + ". Vẫn còn các lỗi trên: sửa phần cấu trúc (làn, hành động, vị trí)."))
    return result


def map_cannot_host(quote: str, graphs: dict[str, LaneGraph]) -> str | None:
    """Why no selected map has one place with everything `quote` asks for together; None when one has."""
    from app.scenario.explore import has_start

    text = _words(quote)
    needed: dict[str, str] = {}
    if CUT_IN_WORDS.search(text):
        needed["multi_lane"] = "làn bên cạnh cùng chiều để tạt đầu"
    if ROAD_WORDS["four_way"].search(text):
        needed["intersection_4way_approach"] = "ngã tư"
    elif ROAD_WORDS["junction"].search(text):
        needed["junction_approach"] = "giao lộ"
    if not needed or any(has_start(graph, set(needed)) for graph in graphs.values()):
        return None
    return f"Map không có chỗ nào có đồng thời: {', '.join(needed.values())}."
