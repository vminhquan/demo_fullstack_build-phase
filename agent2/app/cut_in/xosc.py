"""Deterministic OpenSCENARIO 1.0 export for a validated motorcycle cut-in.

The selected site provides OpenDRIVE lane coordinates. The LLM never writes
XML or invents spawn coordinates; the exporter refuses legacy sites without
the sync-time position metadata needed for a curved road.
"""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET

from app.catalog.models import SelectedSnapshot
from app.cut_in.model import CutInPlan
from app.cut_in.sample import SampledVariant
from app.cut_in.validate import SCENARIO_DURATION_S, validate_cut_in


class XoscExportError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _number(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def _trigger(parent: ET.Element, name: str, seconds: float) -> None:
    condition = ET.SubElement(ET.SubElement(parent, "ConditionGroup"), "Condition", {
        "name": name, "delay": "0", "conditionEdge": "rising",
    })
    ET.SubElement(ET.SubElement(condition, "ByValueCondition"), "SimulationTimeCondition", {
        "value": _number(seconds), "rule": "greaterThan",
    })


# ScenarioRunner names each CARLA actor after its ScenarioObject (role_name = name): "hero" is what CARLA
# tools, and the Bridge's follow camera, look for as the ego.
EGO = "hero"
MOTORCYCLE = "Motorcycle"

# ScenarioRunner turns storyboard stop conditions named criteria_* into its test criteria; without any it prints
# "Nothing to analyze" and writes no --json report. Kept in their own ConditionGroup (groups are OR-ed), so they
# never hold back the time-based end of the scenario.
CRITERIA = ("CollisionTest", "KeepLaneTest", "RunningRedLightTest", "RunningStopTest", "OnSidewalkTest", "WrongLaneTest")


def _criteria(stop_trigger: ET.Element) -> None:
    group = ET.SubElement(stop_trigger, "ConditionGroup")
    for name in CRITERIA:
        condition = ET.SubElement(group, "Condition", {"name": f"criteria_{name}", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(condition, "ByValueCondition"), "ParameterCondition", {
            "parameterRef": "", "value": "", "rule": "lessThan",
        })


def _vehicle(parent: ET.Element, entity: str, blueprint: str, *, motorcycle: bool) -> None:
    object_node = ET.SubElement(parent, "ScenarioObject", {"name": entity})
    vehicle = ET.SubElement(object_node, "Vehicle", {
        "name": blueprint, "vehicleCategory": "motorbike" if motorcycle else "car",
    })
    box = ET.SubElement(vehicle, "BoundingBox")
    ET.SubElement(box, "Center", {"x": "0", "y": "0", "z": "0.9" if motorcycle else "0.8"})
    ET.SubElement(box, "Dimensions", {
        "width": "0.8" if motorcycle else "1.8",
        "length": "2.2" if motorcycle else "4.5",
        "height": "1.8" if motorcycle else "1.6",
    })
    ET.SubElement(vehicle, "Performance", {"maxSpeed": "50", "maxAcceleration": "4", "maxDeceleration": "8"})
    axles = ET.SubElement(vehicle, "Axles")
    for tag, x in (("FrontAxle", "1.2"), ("RearAxle", "-1.2")):
        ET.SubElement(axles, tag, {
            "maxSteering": "0.5", "wheelDiameter": "0.6", "trackWidth": "0.7" if motorcycle else "1.6",
            "positionX": x, "positionZ": "0.3",
        })
    properties = ET.SubElement(vehicle, "Properties")
    # ScenarioRunner takes only `type = ego_vehicle` as the ego, and builds every criteria_* test for the egos:
    # with "car" here it found no ego, so no criteria and no --json report.
    ET.SubElement(properties, "Property", {"name": "type", "value": "simulation" if motorcycle else "ego_vehicle"})
    ET.SubElement(properties, "Property", {"name": "role_name", "value": "scenario" if motorcycle else "hero"})


def _init_actor(parent: ET.Element, entity: str, road_id: int, lane_id: int, s: float, speed_kmh: float) -> None:
    private = ET.SubElement(parent, "Private", {"entityRef": entity})
    teleport = ET.SubElement(ET.SubElement(private, "PrivateAction"), "TeleportAction")
    ET.SubElement(ET.SubElement(teleport, "Position"), "LanePosition", {
        "roadId": str(road_id), "laneId": str(lane_id), "s": _number(s), "offset": "0",
    })
    speed = ET.SubElement(ET.SubElement(private, "PrivateAction"), "LongitudinalAction")
    action = ET.SubElement(speed, "SpeedAction")
    ET.SubElement(action, "SpeedActionDynamics", {
        "dynamicsShape": "step", "value": "0", "dynamicsDimension": "time",
    })
    ET.SubElement(ET.SubElement(action, "SpeedActionTarget"), "AbsoluteTargetSpeed", {
        "value": _number(speed_kmh / 3.6),
    })


def _environment(parent: ET.Element, plan: CutInPlan) -> None:
    conditions = set(plan.environment.weather_conditions)
    hour = plan.environment.time_of_day_hour
    environment = ET.SubElement(ET.SubElement(parent, "EnvironmentAction"), "Environment", {
        "name": plan.environment.profile_id,
    })
    whole, fraction = divmod(hour, 1)
    minute = round(fraction * 60)
    if minute == 60:
        whole, minute = whole + 1, 0
    ET.SubElement(environment, "TimeOfDay", {
        "animation": "false", "dateTime": f"2020-06-01T{int(whole) % 24:02d}:{minute:02d}:00",
    })
    cloud = "rainy" if "rain" in conditions else "cloudy" if "cloudy" in conditions else "free"
    weather = ET.SubElement(environment, "Weather", {"cloudState": cloud})
    ET.SubElement(weather, "Sun", {
        "azimuth": "0", "elevation": "0.8" if 6 <= hour < 18 else "-0.2",
        "intensity": "0.3" if "cloudy" in conditions or "rain" in conditions else "0.8",
    })
    ET.SubElement(weather, "Fog", {"visualRange": "80" if conditions & {"fog", "dust"} else "100000"})
    ET.SubElement(weather, "Precipitation", {
        "precipitationType": "rain" if "rain" in conditions else "dry",
        "intensity": "0.7" if "rain" in conditions else "0",
    })
    ET.SubElement(environment, "RoadCondition", {
        "frictionScaleFactor": _number(plan.environment.friction_scale_factor),
    })


def render_xosc(plan: CutInPlan, variant: SampledVariant, snapshot: SelectedSnapshot) -> tuple[str, str]:
    """Return (XML, SHA-256) after validating the locked catalog and plan."""

    validation = validate_cut_in(plan, variant, snapshot)
    if not validation.valid:
        codes = ", ".join(issue.code for issue in validation.issues)
        raise XoscExportError("PLAN_INVALID", f"Plan không đạt kiểm tra trước khi xuất XOSC: {codes}")
    site = variant.site
    assert site.ego_s is not None and site.motorcycle_s is not None
    assert site.s_direction is not None and site.target_side is not None
    motorcycle_s = site.motorcycle_s + site.s_direction * plan.motorcycle_start_offset_m
    if not math.isfinite(motorcycle_s) or motorcycle_s < 0:
        raise XoscExportError("SITE_POSITION_OUT_OF_RANGE", "OpenDRIVE s của xe máy nằm ngoài road; cần chọn site khác.")

    root = ET.Element("OpenSCENARIO")
    ET.SubElement(root, "FileHeader", {
        "revMajor": "1", "revMinor": "0", "date": "2020-06-01T00:00:00",
        "description": "Scenario Forge motorcycle cut-in", "author": "Scenario Forge agent2",
    })
    ET.SubElement(root, "CatalogLocations")
    ET.SubElement(ET.SubElement(root, "RoadNetwork"), "LogicFile", {"filepath": snapshot.ref.map_name})
    entities = ET.SubElement(root, "Entities")
    _vehicle(entities, EGO, plan.ego_blueprint_id, motorcycle=False)
    _vehicle(entities, MOTORCYCLE, plan.motorcycle_blueprint_id, motorcycle=True)
    storyboard = ET.SubElement(root, "Storyboard")
    init_actions = ET.SubElement(ET.SubElement(storyboard, "Init"), "Actions")
    _environment(ET.SubElement(init_actions, "GlobalAction"), plan)
    _init_actor(init_actions, EGO, site.ego_lane.road_id, site.ego_lane.lane_id, site.ego_s, plan.ego_speed_kmh)
    _init_actor(init_actions, MOTORCYCLE, site.motorcycle_lane.road_id, site.motorcycle_lane.lane_id,
                motorcycle_s, plan.motorcycle_speed_kmh)

    story = ET.SubElement(storyboard, "Story", {"name": "MotorcycleCutIn"})
    act = ET.SubElement(story, "Act", {"name": "CutInAct"})
    group = ET.SubElement(act, "ManeuverGroup", {"name": "MotorcycleManeuver", "maximumExecutionCount": "1"})
    ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {
        "entityRef": MOTORCYCLE,
    })
    maneuver = ET.SubElement(group, "Maneuver", {"name": "CutIn"})
    event = ET.SubElement(maneuver, "Event", {"name": "LaneChange", "priority": "overwrite"})
    action = ET.SubElement(ET.SubElement(event, "Action", {"name": "MoveIntoEgoLane"}), "PrivateAction")
    lane_change = ET.SubElement(ET.SubElement(action, "LateralAction"), "LaneChangeAction")
    ET.SubElement(lane_change, "LaneChangeActionDynamics", {
        "dynamicsShape": "linear", "dynamicsDimension": "distance",
        "value": _number(plan.motorcycle_speed_kmh / 3.6 * plan.lane_change_duration_s),
    })
    ET.SubElement(ET.SubElement(lane_change, "LaneChangeTarget"), "RelativeTargetLane", {
        "entityRef": EGO, "value": "1" if site.target_side == "left" else "-1",
    })
    _trigger(ET.SubElement(event, "StartTrigger"), "StartLaneChange", plan.trigger_time_s)
    _trigger(ET.SubElement(act, "StartTrigger"), "StartAct", 0)
    # Fixed length; validation keeps the lane change ending at least MIN_OBSERVE_S before it.
    end_time = SCENARIO_DURATION_S
    _trigger(ET.SubElement(act, "StopTrigger"), "EndAct", end_time)
    stop = ET.SubElement(storyboard, "StopTrigger")
    _trigger(stop, "EndScenario", end_time)
    _criteria(stop)
    ET.indent(root)
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    return xml, hashlib.sha256(xml.encode("utf-8")).hexdigest()
