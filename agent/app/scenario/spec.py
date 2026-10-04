"""Structured scenario form (ODD + parameter ranges) -> normalized spec, generation constraints and a
deterministic prompt.

The form is the source of truth: `normalize_spec` fits it to what the guardrails and the selected
CARLA map support (every change is reported as an adjustment), and `apply_constraints` keeps the
LLM-generated ScenarioIR inside the approved ranges.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.scenario.grounding import BASE_TYPES, CatalogGrounder, MapProfile
from app.scenario.schemas import ActorSpec, ActorType, RelativePosition, ScenarioIR, TriggerType, WeatherPreset
from app.scenario.validator import ScenarioGuardrailValidator

ScenarioKind = Literal["cut_in", "sudden_brake", "crossing", "oncoming_lane_departure", "door_opening"]
Adversary = Literal["pedestrian", "motorcycle", "cyclist", "car", "truck", "van", "bus"]
RoadChoice = Literal["any", "intersection", "straight", "curve", "highway"]
Objective = Literal["critical", "boundary", "nominal"]

ADVERSARY_TO_ACTOR: dict[str, ActorType] = {
    "pedestrian": ActorType.PEDESTRIAN, "motorcycle": ActorType.MOTORCYCLE, "cyclist": ActorType.BICYCLE,
    "car": ActorType.CAR, "van": ActorType.CAR, "truck": ActorType.TRUCK, "bus": ActorType.TRUCK,
}
ADVERSARY_VI = {"pedestrian": "người đi bộ", "motorcycle": "xe máy", "cyclist": "xe đạp", "car": "ô tô",
                "truck": "xe tải", "van": "xe van", "bus": "xe buýt"}
ROAD_TYPES: dict[str, tuple[str, ...]] = {
    "intersection": ("intersection_4way", "intersection_3way"),
    "straight": ("urban_straight",),
    "curve": ("urban_curve",),
    "highway": ("highway_straight", "highway_merge"),
}
ROAD_VI = {"intersection_4way": "ngã tư", "intersection_3way": "ngã ba", "urban_straight": "đường thẳng trong đô thị",
           "urban_curve": "đường cong", "highway_straight": "đường cao tốc thẳng", "highway_merge": "đoạn nhập làn cao tốc"}
WEATHER_VI = {"clear": "trời quang", "rain": "trời mưa", "heavy_rain": "mưa lớn", "fog": "sương mù"}
LIGHTING_HOUR = {"day": 14, "dusk": 18, "night": 22}
LIGHTING_VI = {"day": "ban ngày", "dusk": "lúc hoàng hôn", "night": "ban đêm"}
SIDE_VI = {"left": "trái", "right": "phải"}

# Hard limits of ScenarioIR / the guardrail validator.
EGO_SPEED_LIMITS = (10.0, 130.0)
ACTOR_SPEED_LIMITS = (0.0, 150.0)
GAP_LIMITS = (5.0, 200.0)
TRIGGER_LIMITS = (3.0, 100.0)


class Range(BaseModel):
    min: float
    max: float

    @property
    def mid(self) -> float:
        return round((self.min + self.max) / 2, 1)

    def clamp(self, value: float) -> float:
        return min(max(value, self.min), self.max)

    def label(self, unit: str) -> str:
        return f"{self.min:g} {unit}" if self.min == self.max else f"{self.min:g}–{self.max:g} {unit}"


class ScenarioSpec(BaseModel):
    """What the user picked in the structured form."""

    goal: str = Field(default="", max_length=2000)
    scenario_type: ScenarioKind = "cut_in"
    adversary_type: Adversary = "motorcycle"
    direction: Literal["left", "right"] = "left"
    road_type: RoadChoice = "any"
    weather: list[Literal["clear", "rain", "heavy_rain", "fog"]] = Field(default_factory=lambda: ["clear"], min_length=1, max_length=4)
    lighting: Literal["day", "dusk", "night"] = "day"
    objective: Objective = "critical"
    ego_speed_kmh: Range = Range(min=25, max=50)
    actor_speed_kmh: Range = Range(min=10, max=40)
    initial_gap_m: Range = Range(min=8, max=30)
    trigger_distance_m: Range = Range(min=5, max=20)
    ego_blueprint: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _dedupe_weather(self) -> "ScenarioSpec":
        self.weather = list(dict.fromkeys(self.weather))
        return self


class GenerationConstraints(BaseModel):
    """Limits the generated ScenarioIR must respect (derived from a normalized spec)."""

    ego_speed_kmh: Range | None = None
    actor_speed_kmh: Range | None = None
    initial_gap_m: Range | None = None
    trigger_distance_m: Range | None = None
    actor_type: ActorType | None = None
    relative_position: RelativePosition | None = None
    trigger: TriggerType | None = None
    weather: WeatherPreset | None = None
    time_of_day_hour: int | None = Field(default=None, ge=0, le=23)
    road_type: str | None = None
    ego_blueprint: str | None = None
    max_actors: int | None = Field(default=None, ge=1, le=6)


class Adjustment(BaseModel):
    field: str
    before: str
    after: str
    reason: str


class NormalizedSpec(BaseModel):
    spec: ScenarioSpec
    constraints: GenerationConstraints
    adjustments: list[Adjustment]
    chosen_weather: str
    road_type: str


class SpecUnsupported(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fit(name: str, value: Range, low: float, high: float, reason: str, out: list[Adjustment], unit: str) -> Range:
    fixed = value
    if fixed.min > fixed.max:
        fixed = Range(min=fixed.max, max=fixed.min)
        out.append(Adjustment(field=name, before=value.label(unit), after=fixed.label(unit), reason="Đầu dải nhỏ hơn cuối dải, đã đảo lại."))
    clamped = Range(min=min(max(fixed.min, low), high), max=min(max(fixed.max, low), high))
    if clamped != fixed:
        out.append(Adjustment(field=name, before=fixed.label(unit), after=clamped.label(unit), reason=reason))
    return clamped


def _position(spec: ScenarioSpec) -> tuple[RelativePosition, TriggerType | None]:
    if spec.scenario_type == "cut_in":
        return (RelativePosition.AHEAD_ADJACENT_LEFT if spec.direction == "left" else RelativePosition.AHEAD_ADJACENT_RIGHT), TriggerType.CUT_IN
    if spec.scenario_type == "sudden_brake":
        return RelativePosition.AHEAD_SAME_LANE, TriggerType.SUDDEN_BRAKE
    if spec.scenario_type == "crossing":
        side = RelativePosition.CROSSING_FROM_LEFT if spec.direction == "left" else RelativePosition.CROSSING_FROM_RIGHT
        return side, TriggerType.JAYWALKING if spec.adversary_type == "pedestrian" else TriggerType.RED_LIGHT_VIOLATION
    if spec.scenario_type == "oncoming_lane_departure":
        return RelativePosition.ONCOMING, TriggerType.LANE_DEPARTURE
    return RelativePosition.AHEAD_ADJACENT_RIGHT, TriggerType.DOOR_OPENING


def _catalog_has(grounder: CatalogGrounder, adversary: str) -> bool:
    if adversary == "pedestrian":
        return bool(grounder.catalog.walkers)
    bases = {"van": {"van"}, "bus": {"bus"}}.get(adversary) or set(BASE_TYPES[ADVERSARY_TO_ACTOR[adversary].value])
    return any((item.base_type or "").lower() in bases for item in grounder.catalog.vehicles)


def _road_type(spec: ScenarioSpec, profile: MapProfile, out: list[Adjustment]) -> str:
    hostable = profile.road_types
    if not hostable:
        raise SpecUnsupported("MAP_HAS_NO_ROAD_TYPES", f"Không tìm thấy đoạn đường dùng được trên {profile.map_name}.")
    wanted = spec.road_type
    if wanted == "any":
        # Crossing traffic and red-light runners need a junction; other kinds default to a straight road.
        preferred = ROAD_TYPES["intersection"] if spec.scenario_type == "crossing" else ("urban_straight", *ROAD_TYPES["intersection"])
        return next((road for road in preferred if road in hostable), hostable[0])
    match = next((road for road in ROAD_TYPES[wanted] if road in hostable), None)
    if match:
        return match
    fallback = next((road for road in ("urban_straight", *ROAD_TYPES["intersection"]) if road in hostable), hostable[0])
    out.append(Adjustment(field="road_type", before=wanted, after=fallback,
                          reason=f"Bản đồ {profile.map_name} không có loại đoạn đường này trong dữ liệu làn."))
    return fallback


def normalize_spec(spec: ScenarioSpec, grounder: CatalogGrounder, seed: int | None = None) -> NormalizedSpec:
    out: list[Adjustment] = []
    spec = spec.model_copy(deep=True)
    profile = grounder.profile()

    if not _catalog_has(grounder, spec.adversary_type):
        raise SpecUnsupported("CATALOG_MISSING_BLUEPRINT",
                              f"Dữ liệu CARLA {grounder.map_name} không có {ADVERSARY_VI[spec.adversary_type]}; hãy chọn tác nhân khác hoặc dữ liệu CARLA khác.")
    if spec.adversary_type == "pedestrian" and spec.scenario_type != "crossing":
        out.append(Adjustment(field="scenario_type", before=spec.scenario_type, after="crossing",
                              reason="Người đi bộ không tạt đầu / phanh / lấn làn; đổi sang băng ngang đường."))
        spec.scenario_type = "crossing"
    if spec.scenario_type == "door_opening" and spec.adversary_type not in ("car", "van"):
        out.append(Adjustment(field="scenario_type", before="door_opening", after="sudden_brake",
                              reason="Chỉ ô tô/xe van mới mở cửa; đổi sang phanh gấp phía trước."))
        spec.scenario_type = "sudden_brake"

    if spec.ego_blueprint:
        allowed = {item.id for item in grounder.catalog.vehicles if (item.base_type or "").lower() in ("car", "van", "truck", "bus")}
        if spec.ego_blueprint not in allowed:
            out.append(Adjustment(field="ego_blueprint", before=spec.ego_blueprint, after=grounder.blueprint("ego"),
                                  reason="Xe ego không có trong dữ liệu CARLA đã chọn."))
            spec.ego_blueprint = None

    spec.ego_speed_kmh = _fit("ego_speed_kmh", spec.ego_speed_kmh, *EGO_SPEED_LIMITS, "Ngoài giới hạn tốc độ ego (10–130 km/h).", out, "km/h")
    actor_high = ScenarioGuardrailValidator.MAX_PEDESTRIAN_SPEED_KMH if spec.adversary_type == "pedestrian" else ACTOR_SPEED_LIMITS[1]
    reason = "Người đi bộ chạy tối đa 15 km/h." if spec.adversary_type == "pedestrian" else "Ngoài giới hạn tốc độ tác nhân (0–150 km/h)."
    spec.actor_speed_kmh = _fit("actor_speed_kmh", spec.actor_speed_kmh, ACTOR_SPEED_LIMITS[0], actor_high, reason, out, "km/h")
    spec.initial_gap_m = _fit("initial_gap_m", spec.initial_gap_m, *GAP_LIMITS, "Ngoài giới hạn khoảng cách ban đầu (5–200 m).", out, "m")
    # The guardrail requires the trigger to fire no farther than the actor starts.
    trigger_high = min(TRIGGER_LIMITS[1], spec.initial_gap_m.max)
    spec.trigger_distance_m = _fit("trigger_distance_m", spec.trigger_distance_m, TRIGGER_LIMITS[0], trigger_high,
                                   "Khoảng cách kích hoạt phải trong 3–100 m và không lớn hơn khoảng cách ban đầu.", out, "m")
    if profile.max_straight_m and spec.initial_gap_m.min > profile.max_straight_m:
        out.append(Adjustment(field="initial_gap_m", before=spec.initial_gap_m.label("m"), after=spec.initial_gap_m.label("m"),
                              reason=f"Đoạn thẳng dài nhất của {grounder.map_name} khoảng {profile.max_straight_m:.0f} m; tác nhân có thể được đặt gần hơn."))

    road_type = _road_type(spec, profile, out)
    if spec.scenario_type == "oncoming_lane_departure" and not profile.has_oncoming_lane:
        out.append(Adjustment(field="scenario_type", before="oncoming_lane_departure", after="oncoming_lane_departure",
                              reason=f"{grounder.map_name} không có đường hai chiều trong dữ liệu làn; tác nhân sẽ được ước lượng vị trí."))

    # One weather per generated case; "Sinh lại" (next seed) rotates through the selection.
    chosen = spec.weather[(seed or 0) % len(spec.weather)]
    weather = WeatherPreset(chosen)
    if chosen == "clear" and spec.lighting in ("night", "dusk"):
        weather = WeatherPreset(spec.lighting)

    position, trigger = _position(spec)
    constraints = GenerationConstraints(
        ego_speed_kmh=spec.ego_speed_kmh,
        actor_speed_kmh=spec.actor_speed_kmh,
        initial_gap_m=spec.initial_gap_m,
        trigger_distance_m=spec.trigger_distance_m,
        actor_type=ADVERSARY_TO_ACTOR[spec.adversary_type],
        relative_position=position,
        trigger=trigger,
        weather=weather,
        time_of_day_hour=LIGHTING_HOUR[spec.lighting],
        road_type=road_type,
        ego_blueprint=spec.ego_blueprint,
        max_actors=1,
    )
    return NormalizedSpec(spec=spec, constraints=constraints, adjustments=out, chosen_weather=chosen, road_type=road_type)


class ChosenValues(BaseModel):
    ego_speed_kmh: float
    actor_speed_kmh: float
    initial_gap_m: float
    trigger_distance_m: float


def _closing_ms(spec: ScenarioSpec, ego: float, actor: float) -> float:
    if spec.adversary_type == "pedestrian" or spec.scenario_type == "sudden_brake":
        return ego / 3.6
    if spec.scenario_type in ("cut_in", "door_opening"):
        return max(ego - actor, 0.0) / 3.6
    if spec.scenario_type == "oncoming_lane_departure":
        return (ego + actor) / 3.6
    return ego / 3.6


def fit_values(spec: ScenarioSpec, values: ChosenValues) -> ChosenValues:
    """Clamp concrete values into the ranges and keep the trigger fair (TTC >= reaction time) when the range allows."""
    ego = spec.ego_speed_kmh.clamp(values.ego_speed_kmh)
    actor = spec.actor_speed_kmh.clamp(values.actor_speed_kmh)
    gap = spec.initial_gap_m.clamp(values.initial_gap_m)
    trigger = min(spec.trigger_distance_m.clamp(values.trigger_distance_m), gap)
    fair = _closing_ms(spec, ego, actor) * ScenarioGuardrailValidator.MIN_FAIR_TTC_SECONDS + 0.5
    if trigger < fair:
        # Start the actor farther away (within its range) so a fair trigger fits before the gap.
        gap = max(gap, spec.initial_gap_m.clamp(fair))
        trigger = min(spec.trigger_distance_m.clamp(fair), gap)
    return ChosenValues(ego_speed_kmh=round(ego, 1), actor_speed_kmh=round(actor, 1), initial_gap_m=round(gap, 1), trigger_distance_m=round(trigger, 1))


def default_values(spec: ScenarioSpec) -> ChosenValues:
    """Deterministic picks per objective: dangerous end, middle (PASS/FAIL boundary) or the calm end."""
    if spec.objective == "critical":
        raw = ChosenValues(ego_speed_kmh=spec.ego_speed_kmh.max, actor_speed_kmh=spec.actor_speed_kmh.min,
                           initial_gap_m=spec.initial_gap_m.min, trigger_distance_m=spec.trigger_distance_m.min)
    elif spec.objective == "boundary":
        raw = ChosenValues(ego_speed_kmh=spec.ego_speed_kmh.mid, actor_speed_kmh=spec.actor_speed_kmh.mid,
                           initial_gap_m=spec.initial_gap_m.mid, trigger_distance_m=spec.trigger_distance_m.mid)
    else:
        raw = ChosenValues(ego_speed_kmh=spec.ego_speed_kmh.min, actor_speed_kmh=spec.actor_speed_kmh.max,
                           initial_gap_m=spec.initial_gap_m.max, trigger_distance_m=spec.trigger_distance_m.max)
    return fit_values(spec, raw)


def describe(normalized: NormalizedSpec, values: ChosenValues) -> str:
    """Deterministic Vietnamese prompt (keywords the offline generator also understands)."""
    spec = normalized.spec
    who = ADVERSARY_VI[spec.adversary_type]
    side = SIDE_VI[spec.direction]
    where = ROAD_VI.get(normalized.road_type, normalized.road_type)
    weather = WEATHER_VI[normalized.chosen_weather]
    light = LIGHTING_VI[spec.lighting]
    gap, trig, ego, act = values.initial_gap_m, values.trigger_distance_m, values.ego_speed_kmh, values.actor_speed_kmh
    action = {
        "cut_in": f"{who.capitalize()} ở làn bên {side} phía trước, cách ego {gap:g} m, chạy {act:g} km/h rồi tạt đầu (cut-in) sang làn của ego khi còn cách {trig:g} m",
        "sudden_brake": f"{who.capitalize()} chạy phía trước cùng làn, cách ego {gap:g} m, tốc độ {act:g} km/h, phanh gấp khi ego còn cách {trig:g} m",
        "crossing": (f"Người đi bộ băng qua đường từ bên {side}, cách ego {gap:g} m, đi {act:g} km/h, bắt đầu băng ra khi ego còn cách {trig:g} m"
                     if spec.adversary_type == "pedestrian" else
                     f"{who.capitalize()} vượt đèn đỏ cắt ngang từ bên {side}, cách ego {gap:g} m, chạy {act:g} km/h, lao vào khi ego còn cách {trig:g} m"),
        "oncoming_lane_departure": f"{who.capitalize()} chạy ngược chiều cách ego {gap:g} m, tốc độ {act:g} km/h, lấn sang làn của ego khi còn cách {trig:g} m",
        "door_opening": f"{who.capitalize()} đỗ ven đường bên phải phía trước, cách ego {gap:g} m, đột ngột mở cửa khi ego còn cách {trig:g} m",
    }[spec.scenario_type]
    text = f"Tại {where}, {weather}, {light}. Ego chạy {ego:g} km/h. {action}."
    if spec.goal.strip():
        text += f" Mục tiêu kiểm thử: {spec.goal.strip()}"
    return text


def _warn(warnings: list[str], label: str, before: object, after: object) -> None:
    if before != after:
        warnings.append(f"Form: {label} {before} → {after} để nằm trong dải đã chọn")


def _validator_closing_ms(ego_kmh: float, actor: ActorSpec) -> float:
    """Same closing speed as ScenarioGuardrailValidator's TTC fairness rule."""
    if actor.actor_type == ActorType.PEDESTRIAN or actor.trigger in (TriggerType.JAYWALKING, TriggerType.RED_LIGHT_VIOLATION):
        return ego_kmh / 3.6
    if actor.relative_position == RelativePosition.ONCOMING:
        return (ego_kmh + actor.initial_speed_kmh) / 3.6
    if actor.relative_position == RelativePosition.BEHIND_SAME_LANE:
        return (actor.initial_speed_kmh - ego_kmh) / 3.6
    if actor.trigger == TriggerType.SUDDEN_BRAKE:
        return ego_kmh / 3.6
    return (ego_kmh - actor.initial_speed_kmh) / 3.6


def _keep_fair(ego, actor: ActorSpec, c: GenerationConstraints, warnings: list[str]) -> None:
    """Clamping can leave the trigger too late (TTC < reaction time). Inside the form's ranges: trigger later,
    then start farther, then slow the ego. Whatever still fails is left for the guardrail to report."""
    if actor.trigger is None or actor.trigger_distance_m is None:
        return
    ttc = ScenarioGuardrailValidator.MIN_FAIR_TTC_SECONDS
    needed = _validator_closing_ms(ego.initial_speed_kmh, actor) * ttc + 0.3
    if actor.trigger_distance_m >= needed:
        return
    gap_range = c.initial_gap_m or Range(min=actor.initial_distance_m, max=actor.initial_distance_m)
    trig_range = c.trigger_distance_m or Range(min=3.0, max=100.0)
    before = (actor.trigger_distance_m, actor.initial_distance_m, ego.initial_speed_kmh)
    actor.initial_distance_m = round(max(actor.initial_distance_m, gap_range.clamp(needed)), 1)
    actor.trigger_distance_m = round(min(trig_range.clamp(needed), actor.initial_distance_m), 1)
    if actor.trigger_distance_m < needed and c.ego_speed_kmh:
        # Highest ego speed that keeps TTC fair at this trigger distance.
        offset = _validator_closing_ms(ego.initial_speed_kmh, actor) * 3.6 - ego.initial_speed_kmh
        fair_speed = (actor.trigger_distance_m - 0.3) / ttc * 3.6 - offset
        ego.initial_speed_kmh = round(c.ego_speed_kmh.clamp(min(ego.initial_speed_kmh, fair_speed)), 1)
    after = (actor.trigger_distance_m, actor.initial_distance_m, ego.initial_speed_kmh)
    if after != before:
        warnings.append(f"Form: giữ TTC ≥ {ttc}s — kích hoạt {before[0]}→{after[0]} m, cách {before[1]}→{after[1]} m, ego {before[2]}→{after[2]} km/h")


def apply_constraints(ir: ScenarioIR, constraints: GenerationConstraints | None) -> tuple[ScenarioIR, list[str]]:
    """Force the IR into the form's limits. Returns the adjusted IR and one warning per change."""
    if constraints is None:
        return ir, []
    c, warnings = constraints, []
    ego = ir.ego.model_copy()
    if c.ego_speed_kmh:
        speed = round(c.ego_speed_kmh.clamp(ego.initial_speed_kmh), 1)
        _warn(warnings, "tốc độ ego", ego.initial_speed_kmh, speed)
        ego.initial_speed_kmh = speed
    if c.road_type:
        ego.road_type = c.road_type

    actors = list(ir.actors)
    primary_index = next((i for i, a in enumerate(actors) if c.actor_type is None or a.actor_type == c.actor_type), 0)
    primary = actors[primary_index].model_copy()
    if c.actor_type and primary.actor_type != c.actor_type:
        _warn(warnings, "loại tác nhân", primary.actor_type.value, c.actor_type.value)
        primary.actor_type = c.actor_type
    if c.relative_position and primary.relative_position != c.relative_position:
        _warn(warnings, "vị trí tác nhân", primary.relative_position.value, c.relative_position.value)
        primary.relative_position = c.relative_position
    if c.trigger and primary.trigger != c.trigger:
        _warn(warnings, "hành vi", primary.trigger.value if primary.trigger else "none", c.trigger.value)
        primary.trigger = c.trigger
    if c.actor_speed_kmh:
        speed = round(c.actor_speed_kmh.clamp(primary.initial_speed_kmh), 1)
        _warn(warnings, "tốc độ tác nhân", primary.initial_speed_kmh, speed)
        primary.initial_speed_kmh = speed
    if c.initial_gap_m:
        gap = round(c.initial_gap_m.clamp(primary.initial_distance_m), 1)
        _warn(warnings, "khoảng cách ban đầu", primary.initial_distance_m, gap)
        primary.initial_distance_m = gap
    if c.trigger_distance_m and primary.trigger is not None:
        current = primary.trigger_distance_m if primary.trigger_distance_m is not None else c.trigger_distance_m.mid
        trig = round(min(c.trigger_distance_m.clamp(current), primary.initial_distance_m), 1)
        if primary.trigger_distance_m is not None:
            _warn(warnings, "khoảng cách kích hoạt", primary.trigger_distance_m, trig)
        primary.trigger_distance_m = trig
    _keep_fair(ego, primary, c, warnings)
    actors[primary_index] = ActorSpec(**primary.model_dump())
    if c.max_actors and len(actors) > c.max_actors:
        warnings.append(f"Form: bỏ {len(actors) - c.max_actors} tác nhân thêm ngoài form")
        actors = [actors[primary_index]][: c.max_actors]

    update: dict = {"ego": ego, "actors": actors}
    if c.weather and ir.weather != c.weather:
        _warn(warnings, "thời tiết", ir.weather.value, c.weather.value)
        update["weather"] = c.weather
    if c.time_of_day_hour is not None:
        update["time_of_day_hour"] = c.time_of_day_hour
    return ScenarioIR(**ir.model_copy(update=update).model_dump()), warnings
