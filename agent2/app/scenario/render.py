"""Deterministic OpenSCENARIO 1.0 export of a checked ScenarioPlan.

Any number of vehicles, each teleported to its resolved lane position with its start speed, then its timed actions:
speed changes (linear over the time the rate needs), lane changes (by distance, as ScenarioRunner drives them) and
braking to a stop. The LLM never writes XML or coordinates.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from app.scenario.physics import LIMITS
from app.scenario.spec import ResolvedAction, ResolvedActor, ScenarioPlan

# ScenarioRunner names each CARLA actor after its ScenarioObject (role_name = name): "hero" is what CARLA tools, and
# the Bridge's follow camera, look for as the ego.
EGO = "hero"
# ScenarioRunner turns storyboard stop conditions named criteria_* into its test criteria; without any it prints
# "Nothing to analyze" and writes no --json report. Kept in their own ConditionGroup (groups are OR-ed), so they
# never hold back the time-based end of the scenario.
CRITERIA = ("CollisionTest", "KeepLaneTest", "RunningRedLightTest", "RunningStopTest", "OnSidewalkTest", "WrongLaneTest")


def _number(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def _time_trigger(parent: ET.Element, name: str, seconds: float) -> None:
    condition = ET.SubElement(ET.SubElement(parent, "ConditionGroup"), "Condition", {
        "name": name, "delay": "0", "conditionEdge": "rising",
    })
    ET.SubElement(ET.SubElement(condition, "ByValueCondition"), "SimulationTimeCondition", {
        "value": _number(seconds), "rule": "greaterThan",
    })


def _criteria(stop_trigger: ET.Element) -> None:
    group = ET.SubElement(stop_trigger, "ConditionGroup")
    for name in CRITERIA:
        condition = ET.SubElement(group, "Condition", {"name": f"criteria_{name}", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(condition, "ByValueCondition"), "ParameterCondition", {
            "parameterRef": "", "value": "", "rule": "lessThan",
        })


def _vehicle(parent: ET.Element, name: str, actor: ResolvedActor, *, ego: bool) -> None:
    limits = LIMITS[actor.kind]
    vehicle = ET.SubElement(ET.SubElement(parent, "ScenarioObject", {"name": name}), "Vehicle", {
        "name": actor.blueprint_id, "vehicleCategory": limits.category,
    })
    box = ET.SubElement(vehicle, "BoundingBox")
    ET.SubElement(box, "Center", {"x": "0", "y": "0", "z": _number(actor.height_m / 2)})
    ET.SubElement(box, "Dimensions", {
        "width": _number(actor.width_m), "length": _number(actor.length_m), "height": _number(actor.height_m),
    })
    ET.SubElement(vehicle, "Performance", {
        "maxSpeed": _number(limits.max_speed_kmh / 3.6), "maxAcceleration": _number(limits.max_accel_mps2),
        "maxDeceleration": _number(limits.max_decel_mps2),
    })
    axles = ET.SubElement(vehicle, "Axles")
    half = actor.length_m * 0.3
    for tag, x in (("FrontAxle", half), ("RearAxle", -half)):
        ET.SubElement(axles, tag, {
            "maxSteering": "0.5", "wheelDiameter": "0.6", "trackWidth": _number(max(actor.width_m - 0.2, 0.3)),
            "positionX": _number(x), "positionZ": "0.3",
        })
    properties = ET.SubElement(vehicle, "Properties")
    # ScenarioRunner takes only `type = ego_vehicle` as the ego, and builds every criteria_* test for the egos.
    ET.SubElement(properties, "Property", {"name": "type", "value": "ego_vehicle" if ego else "simulation"})
    ET.SubElement(properties, "Property", {"name": "role_name", "value": "hero" if ego else "scenario"})


def _speed_action(parent: ET.Element, speed_mps: float, seconds: float) -> None:
    action = ET.SubElement(ET.SubElement(parent, "LongitudinalAction"), "SpeedAction")
    ET.SubElement(action, "SpeedActionDynamics", {
        "dynamicsShape": "linear" if seconds > 0 else "step", "value": _number(seconds), "dynamicsDimension": "time",
    })
    ET.SubElement(ET.SubElement(action, "SpeedActionTarget"), "AbsoluteTargetSpeed", {"value": _number(speed_mps)})


def _init_actor(parent: ET.Element, name: str, actor: ResolvedActor) -> None:
    private = ET.SubElement(parent, "Private", {"entityRef": name})
    teleport = ET.SubElement(ET.SubElement(private, "PrivateAction"), "TeleportAction")
    ET.SubElement(ET.SubElement(teleport, "Position"), "LanePosition", {
        "roadId": str(actor.road_id), "laneId": str(actor.lane_id), "s": _number(actor.s), "offset": "0",
    })
    _speed_action(ET.SubElement(private, "PrivateAction"), actor.speed_kmh / 3.6, 0.0)


def _event(maneuver: ET.Element, index: int, action: ResolvedAction, limits) -> None:
    event = ET.SubElement(maneuver, "Event", {"name": f"{action.actor}_{index}_{action.type}", "priority": "parallel"})
    private = ET.SubElement(ET.SubElement(event, "Action", {"name": f"{action.type}_{index}"}), "PrivateAction")
    start_mps = action.speed_at_start_kmh / 3.6
    if action.type == "lane_change":
        lane_change = ET.SubElement(ET.SubElement(private, "LateralAction"), "LaneChangeAction")
        # ScenarioRunner drives lane changes by distance: the distance covered at the actor's speed over the duration.
        ET.SubElement(lane_change, "LaneChangeActionDynamics", {
            "dynamicsShape": "linear", "dynamicsDimension": "distance",
            "value": _number(max(start_mps * float(action.duration_s or 0), 1.0)),
        })
        ET.SubElement(ET.SubElement(lane_change, "LaneChangeTarget"), "RelativeTargetLane", {
            "entityRef": action.actor, "value": "1" if (action.resolved_direction or action.direction) == "left" else "-1",
        })
    else:
        target = 0.0 if action.type == "brake_to_stop" else float(action.target_speed_kmh or 0) / 3.6
        default = limits.max_decel_mps2 if target < start_mps else limits.max_accel_mps2
        rate = abs(float(action.acceleration_mps2 or default))
        _speed_action(private, target, abs(target - start_mps) / rate if rate else 0.0)
    _time_trigger(ET.SubElement(event, "StartTrigger"), f"start_{action.actor}_{index}", action.at_time_s)


def _hold_until(act: ET.Element, seconds: float, speed_mps: float) -> None:
    """ScenarioRunner ends an Act as soon as its maneuver groups are done, before the Act StopTrigger: this group's
    event only starts at `seconds`, so the scenario lasts that long. Its action re-sets the ego's own speed."""
    group = ET.SubElement(act, "ManeuverGroup", {"name": "Timeline", "maximumExecutionCount": "1"})
    ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": EGO})
    event = ET.SubElement(ET.SubElement(group, "Maneuver", {"name": "HoldUntilEnd"}), "Event",
                          {"name": "HoldUntilEnd", "priority": "parallel"})
    _speed_action(ET.SubElement(ET.SubElement(event, "Action", {"name": "KeepEgoSpeed"}), "PrivateAction"), speed_mps, 0.0)
    _time_trigger(ET.SubElement(event, "StartTrigger"), "ScenarioEnd", seconds)


def _environment(parent: ET.Element, plan: ScenarioPlan) -> None:
    conditions = set(plan.environment.weather_conditions)
    hour = plan.environment.time_of_day_hour
    environment = ET.SubElement(ET.SubElement(parent, "EnvironmentAction"), "Environment", {"name": plan.environment.profile_id})
    whole, fraction = divmod(hour, 1)
    minute = round(fraction * 60)
    if minute == 60:
        whole, minute = whole + 1, 0
    ET.SubElement(environment, "TimeOfDay", {"animation": "false", "dateTime": f"2020-06-01T{int(whole) % 24:02d}:{minute:02d}:00"})
    cloud = "rainy" if "rain" in conditions else "cloudy" if "cloudy" in conditions else "free"
    weather = ET.SubElement(environment, "Weather", {"cloudState": cloud})
    ET.SubElement(weather, "Sun", {
        "azimuth": "0", "elevation": "0.8" if 6 <= hour < 18 else "-0.2",
        "intensity": "0.3" if "cloudy" in conditions or "rain" in conditions else "0.8",
    })
    ET.SubElement(weather, "Fog", {"visualRange": "80" if conditions & {"fog", "dust"} else "100000"})
    ET.SubElement(weather, "Precipitation", {
        "precipitationType": "rain" if "rain" in conditions else "dry", "intensity": "0.7" if "rain" in conditions else "0",
    })
    ET.SubElement(environment, "RoadCondition", {"frictionScaleFactor": _number(plan.environment.friction_scale_factor)})


def render_xosc(plan: ScenarioPlan, map_name: str) -> tuple[str, str]:
    """Return (XML, SHA-256)."""
    root = ET.Element("OpenSCENARIO")
    ET.SubElement(root, "FileHeader", {
        "revMajor": "1", "revMinor": "0", "date": "2020-06-01T00:00:00",
        "description": f"Scenario Forge: {plan.title}"[:200], "author": "Scenario Forge agent2",
    })
    ET.SubElement(root, "CatalogLocations")
    ET.SubElement(ET.SubElement(root, "RoadNetwork"), "LogicFile", {"filepath": map_name})
    entities = ET.SubElement(root, "Entities")
    _vehicle(entities, EGO, plan.ego, ego=True)
    for actor in plan.actors:
        _vehicle(entities, actor.id, actor, ego=False)
    storyboard = ET.SubElement(root, "Storyboard")
    init_actions = ET.SubElement(ET.SubElement(storyboard, "Init"), "Actions")
    _environment(ET.SubElement(init_actions, "GlobalAction"), plan)
    _init_actor(init_actions, EGO, plan.ego)
    for actor in plan.actors:
        _init_actor(init_actions, actor.id, actor)

    act = ET.SubElement(ET.SubElement(storyboard, "Story", {"name": "Scenario"}), "Act", {"name": "MainAct"})
    by_actor = {actor.id: actor for actor in plan.actors}
    for actor in plan.actors:
        actions = [(index, action) for index, action in enumerate(plan.actions) if action.actor == actor.id]
        if not actions:
            continue
        group = ET.SubElement(act, "ManeuverGroup", {"name": f"{actor.id}_maneuvers", "maximumExecutionCount": "1"})
        ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": actor.id})
        maneuver = ET.SubElement(group, "Maneuver", {"name": f"{actor.id}_maneuver"})
        for index, action in actions:
            _event(maneuver, index, action, LIMITS[by_actor[action.actor].kind])
    _hold_until(act, plan.duration_s, plan.ego.speed_kmh / 3.6)
    _time_trigger(ET.SubElement(act, "StartTrigger"), "StartAct", 0)
    _time_trigger(ET.SubElement(act, "StopTrigger"), "EndAct", plan.duration_s)
    stop = ET.SubElement(storyboard, "StopTrigger")
    _time_trigger(stop, "EndScenario", plan.duration_s)
    _criteria(stop)
    ET.indent(root)
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    return xml, hashlib.sha256(xml.encode("utf-8")).hexdigest()
