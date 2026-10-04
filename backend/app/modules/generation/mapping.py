"""Map an Agent result onto the 5 required test-case fields. Pure functions — no I/O.

The values are a suggestion: the creator reviews and may edit them before saving the DRAFT.
"""
from __future__ import annotations

from typing import Any

from app.shared.infrastructure.models import DangerLevel

# ScenarioIR actor_type -> adversary_type vocabulary used by existing test cases (docs/14).
ADVERSARY_TYPES = {"car": "car", "motorcycle": "motorcycle", "bicycle": "cyclist", "pedestrian": "pedestrian", "truck": "truck"}

# weighted_threat (0..1, ScenGE 5D threat score) -> danger level. Thresholds are a starting point,
# chosen from the spread of generated scenarios, not a calibrated standard.
DANGER_THRESHOLDS: tuple[tuple[float, DangerLevel], ...] = (
    (0.30, DangerLevel.LOW),
    (0.45, DangerLevel.MEDIUM),
    (0.60, DangerLevel.HIGH),
)
DANGER_ORDER = [DangerLevel.LOW, DangerLevel.MEDIUM, DangerLevel.HIGH, DangerLevel.CRITICAL]


def primary_actor(ir: dict[str, Any]) -> dict[str, Any]:
    actors = ir.get("actors") or [{}]
    return next((actor for actor in actors if actor.get("trigger")), actors[0])


def adversary_type(ir: dict[str, Any]) -> str:
    actor_type = str(primary_actor(ir).get("actor_type", "car"))
    return ADVERSARY_TYPES.get(actor_type, actor_type)


# Precipitation after dark keeps both facts by using the CARLA preset name (reversed by environment_constraints).
WET_AFTER_DARK = {("rain", "night"): "MidRainyNight", ("heavy_rain", "night"): "HardRainNight",
                  ("rain", "dusk"): "MidRainSunset", ("heavy_rain", "dusk"): "HardRainSunset"}


def environment_code(ir: dict[str, Any]) -> str:
    weather = str(ir.get("weather", "clear"))
    hour = int(ir.get("time_of_day_hour", 12))
    light = "night" if (hour >= 19 or hour < 6) else "dusk" if hour >= 17 else "day"
    if weather == "clear" and light != "day":
        return light
    return WET_AFTER_DARK.get((weather, light), weather)


def danger_level(threat_score: dict[str, Any], scenario_ir: dict[str, Any]) -> DangerLevel:
    threat = float(threat_score.get("weighted_threat", 0.0))
    level = next((value for limit, value in DANGER_THRESHOLDS if threat < limit), DangerLevel.CRITICAL)
    expected = ((scenario_ir.get("expected_outcome") or {}).get("expected_verdict")) or ""
    if expected == "COLLISION_EXPECTED" and DANGER_ORDER.index(level) < DANGER_ORDER.index(DangerLevel.HIGH):
        level = DangerLevel.HIGH
    return level


def suggested_version(result: dict[str, Any]) -> dict[str, Any]:
    ir = result["scenario_ir"]
    grounding = result.get("grounding") or {}
    tags = ["ai-generated", str(ir.get("ego", {}).get("road_type", "")).replace("_", "-")]
    trigger = primary_actor(ir).get("trigger")
    if trigger:
        tags.append(str(trigger).replace("_", "-"))
    return {
        "map_code": grounding.get("map_name") or ir.get("map_name"),
        "ego_vehicle_code": (grounding.get("ego") or {}).get("blueprint", "vehicle.tesla.model3"),
        "adversary_type": adversary_type(ir),
        "environment_code": environment_code(ir),
        "danger_level": danger_level(result.get("threat_score") or {}, ir).value,
        "tag_names": [tag for tag in tags if tag],
    }


# ---------------------------------------------------------------- metadata -> Agent constraints
# The reverse direction: the 5 metadata a creator picked on /test-cases/new become generation
# constraints (agent/app/scenario/spec.py GenerationConstraints), so the .xosc matches them.
ACTOR_FROM_ADVERSARY = {
    "pedestrian": "pedestrian", "motorcycle": "motorcycle", "cyclist": "bicycle",
    "car": "car", "van": "car", "truck": "truck", "bus": "truck",
}
STANDARD_ENVIRONMENTS = {
    "clear": ("clear", 12), "rain": ("rain", 14), "heavy_rain": ("heavy_rain", 14),
    "fog": ("fog", 10), "night": ("night", 22), "dusk": ("dusk", 18),
}


def environment_constraints(code: str) -> tuple[str, int]:
    """Standard code, or a CARLA preset such as HardRainNight / WetCloudySunset -> (weather, hour)."""
    if code in STANDARD_ENVIRONMENTS:
        return STANDARD_ENVIRONMENTS[code]
    hour = 22 if "Night" in code else 18 if "Sunset" in code else 14
    if "HardRain" in code:
        weather = "heavy_rain"
    elif "Rain" in code:
        weather = "rain"
    elif "DustStorm" in code:
        weather = "fog"  # closest visibility-limiting preset the Agent knows
    elif hour == 22:
        weather = "night"
    elif hour == 18:
        weather = "dusk"
    else:
        weather = "clear"
    return weather, hour


def metadata_constraints(
    *, ego_vehicle_code: str | None, adversary_type: str | None, environment_code: str | None
) -> dict[str, Any]:
    """Constraints for the values the user picked; anything left out stays the Agent's choice."""
    constraints: dict[str, Any] = {}
    if environment_code:
        constraints["weather"], constraints["time_of_day_hour"] = environment_constraints(environment_code)
    if ego_vehicle_code:
        constraints["ego_blueprint"] = ego_vehicle_code
    if adversary_type in ACTOR_FROM_ADVERSARY:
        constraints["actor_type"] = ACTOR_FROM_ADVERSARY[adversary_type]
    return constraints
