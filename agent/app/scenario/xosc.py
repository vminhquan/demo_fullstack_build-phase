"""
OpenSCENARIO 1.0 (.xosc) export for map-grounded scenarios, plus XSD validation.

Structure follows ScenarioForge's original exporter; the poses now come from `grounding` (real
lanes of the selected catalog) instead of hard-coded road anchors.
"""
from __future__ import annotations

import math
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.scenario.grounding import GroundedScenario, PlacedEntity, right_of
from app.scenario.schemas import ActorType, WeatherPreset

XSD_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "schemas" / "OpenSCENARIO_1_0.xsd"

# WeatherPreset -> CARLA-like weather knobs. Precipitation stays <= 30 (DX11 shader safety, see P-065 AGENTS.md).
WEATHER_MAP: dict[WeatherPreset, dict[str, float]] = {
    WeatherPreset.CLEAR: {"sun_altitude_angle": 70.0, "cloudiness": 10.0, "precipitation": 0.0, "fog_density": 0.0},
    WeatherPreset.RAIN: {"sun_altitude_angle": 50.0, "cloudiness": 80.0, "precipitation": 30.0, "fog_density": 10.0},
    WeatherPreset.HEAVY_RAIN: {"sun_altitude_angle": 30.0, "cloudiness": 90.0, "precipitation": 30.0, "fog_density": 20.0},
    WeatherPreset.FOG: {"sun_altitude_angle": 40.0, "cloudiness": 60.0, "precipitation": 0.0, "fog_density": 80.0},
    WeatherPreset.NIGHT: {"sun_altitude_angle": -30.0, "cloudiness": 20.0, "precipitation": 0.0, "fog_density": 5.0},
    WeatherPreset.DUSK: {"sun_altitude_angle": 5.0, "cloudiness": 30.0, "precipitation": 0.0, "fog_density": 10.0},
}

VEHICLE_CATEGORY: dict[str, str] = {
    ActorType.CAR.value: "car",
    ActorType.MOTORCYCLE.value: "motorbike",
    ActorType.BICYCLE.value: "bicycle",
    ActorType.TRUCK.value: "truck",
}

STOP_AFTER_SECONDS = 20.0


def toward_ego_lane(actor: PlacedEntity, ego: PlacedEntity) -> str:
    """RelativeTargetLane value that moves `actor` one lane toward the ego: "1" (left) when it is on the ego's right.

    ScenarioRunner ignores entityRef and reads the value as lane changes from the actor's own lane (> 0 = left,
    openscenario_parser.py:1383); "0" ("the ego's lane" in OpenSCENARIO) makes it divide by zero.
    Positions are CARLA world coordinates, before the y flip of the XOSC.
    """
    rx, ry = right_of(ego.yaw)
    lateral = (actor.x - ego.x) * rx + (actor.y - ego.y) * ry
    return "1" if lateral > 0 else "-1"


class XoscExporter:
    def __init__(self, *, flip_y: bool = True) -> None:
        # OpenSCENARIO is right-handed; CARLA is left-handed. ScenarioRunner negates y and heading
        # when it reads WorldPosition, so CARLA poses are written mirrored. [Unverified against every
        # ScenarioRunner release — keep XOSC_FLIP_Y configurable.]
        self.sign = -1.0 if flip_y else 1.0

    def _world_position(self, parent: ET.Element, x: float, y: float, z: float, yaw_deg: float) -> None:
        ET.SubElement(parent, "WorldPosition", {
            "x": f"{x:.3f}",
            "y": f"{self.sign * y:.3f}",
            "z": f"{z:.3f}",
            "h": f"{math.radians(self.sign * yaw_deg):.4f}",
        })

    def to_xml_string(self, grounded: GroundedScenario) -> str:
        ir = grounded.ir
        root = ET.Element("OpenSCENARIO")
        ET.SubElement(root, "FileHeader", {
            "revMajor": "1",
            "revMinor": "0",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "description": f"ScenarioForge: {ir.name}",
            "author": "ScenarioForge Agent",
        })
        ET.SubElement(root, "ParameterDeclarations")
        ET.SubElement(root, "CatalogLocations")
        road_network = ET.SubElement(root, "RoadNetwork")
        # ScenarioRunner loads a CARLA town by name when LogicFile is not an .xodr path.
        ET.SubElement(road_network, "LogicFile", {"filepath": ir.map_name})
        ET.SubElement(road_network, "SceneGraphFile", {"filepath": ""})

        entities = ET.SubElement(root, "Entities")
        for entity in [grounded.ego, *grounded.actors]:
            self._entity(entities, entity, is_ego=entity is grounded.ego)

        storyboard = ET.SubElement(root, "Storyboard")
        actions = ET.SubElement(ET.SubElement(storyboard, "Init"), "Actions")
        self._environment(actions, ir.weather, ir.time_of_day_hour)
        self._init_private(actions, grounded.ego)
        for actor in grounded.actors:
            self._init_private(actions, actor)

        story = ET.SubElement(storyboard, "Story", {"name": f"Story_{ir.name}"})
        act_el = ET.SubElement(story, "Act", {"name": f"Act_{ir.name}"})
        self._route_maneuver(act_el, grounded.ego, grounded.route)
        for idx, actor in enumerate(grounded.actors):
            self._maneuver(act_el, idx, actor, grounded.ego)
        act_start = ET.SubElement(ET.SubElement(ET.SubElement(act_el, "StartTrigger"), "ConditionGroup"), "Condition",
                                  {"name": "ActStart", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(act_start, "ByValueCondition"), "SimulationTimeCondition", {"value": "0", "rule": "greaterThan"})
        # ScenarioRunner ends a scenario only through an Act StopTrigger; it reads nothing but criteria_* from
        # the Storyboard StopTrigger, so without this a maneuver that never completes runs forever.
        act_stop = ET.SubElement(ET.SubElement(ET.SubElement(act_el, "StopTrigger"), "ConditionGroup"), "Condition",
                                 {"name": "ActEnd", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(act_stop, "ByValueCondition"), "SimulationTimeCondition",
                      {"value": f"{STOP_AFTER_SECONDS}", "rule": "greaterThan"})
        stop = ET.SubElement(ET.SubElement(ET.SubElement(storyboard, "StopTrigger"), "ConditionGroup"), "Condition",
                             {"name": "EndSim", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(stop, "ByValueCondition"), "SimulationTimeCondition",
                      {"value": f"{STOP_AFTER_SECONDS}", "rule": "greaterThan"})

        ET.indent(root)
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"

    @staticmethod
    def _entity(entities: ET.Element, entity: PlacedEntity, *, is_ego: bool) -> None:
        obj = ET.SubElement(entities, "ScenarioObject", {"name": entity.entity_name})
        if entity.is_walker:
            ped = ET.SubElement(obj, "Pedestrian", {"model": entity.blueprint, "mass": "75.0", "name": entity.blueprint, "pedestrianCategory": "pedestrian"})
            ET.SubElement(ped, "ParameterDeclarations")
            box = ET.SubElement(ped, "BoundingBox")
            ET.SubElement(box, "Center", {"x": "0.0", "y": "0.0", "z": "0.9"})
            ET.SubElement(box, "Dimensions", {"width": "0.6", "length": "0.6", "height": "1.8"})
            props = ET.SubElement(ped, "Properties")
            ET.SubElement(props, "Property", {"name": "type", "value": "simulation"})
            return
        vehicle = ET.SubElement(obj, "Vehicle", {"name": entity.blueprint, "vehicleCategory": VEHICLE_CATEGORY.get(entity.actor_type, "car")})
        ET.SubElement(vehicle, "ParameterDeclarations")
        box = ET.SubElement(vehicle, "BoundingBox")
        ET.SubElement(box, "Center", {"x": "1.4", "y": "0.0", "z": "0.8"})
        ET.SubElement(box, "Dimensions", {"width": "2.0", "length": "4.5", "height": "1.6"})
        ET.SubElement(vehicle, "Performance", {"maxSpeed": "69.444", "maxAcceleration": "10.0", "maxDeceleration": "10.0"})
        axles = ET.SubElement(vehicle, "Axles")
        for axle, position_x, steering in (("FrontAxle", "3.1", "0.5"), ("RearAxle", "0.0", "0.0")):
            ET.SubElement(axles, axle, {"maxSteering": steering, "wheelDiameter": "0.6", "trackWidth": "1.8", "positionX": position_x, "positionZ": "0.3"})
        props = ET.SubElement(vehicle, "Properties")
        ET.SubElement(props, "Property", {"name": "type", "value": "ego_vehicle" if is_ego else "simulation"})

    @staticmethod
    def _environment(actions: ET.Element, weather: WeatherPreset, hour: int) -> None:
        knobs = WEATHER_MAP.get(weather, WEATHER_MAP[WeatherPreset.CLEAR])
        env_action = ET.SubElement(ET.SubElement(actions, "GlobalAction"), "EnvironmentAction")
        env = ET.SubElement(env_action, "Environment", {"name": f"Env_{weather.value}"})
        ET.SubElement(env, "TimeOfDay", {"animation": "false", "dateTime": f"2026-09-26T{hour:02d}:00:00"})
        precipitation = min(30.0, knobs.get("precipitation", 0.0))
        cloud_state = "rainy" if precipitation > 0 else "cloudy" if knobs.get("cloudiness", 0.0) > 50 else "free"
        weather_el = ET.SubElement(env, "Weather", {"cloudState": cloud_state})
        sun = knobs.get("sun_altitude_angle", 60.0)
        # The hour decides the light too (e.g. rain at night): the sun sets after 17h and is gone after 20h.
        if hour >= 20 or hour < 5:
            sun = min(sun, -30.0)
        elif hour >= 17:
            sun = min(sun, 5.0)
        ET.SubElement(weather_el, "Sun", {"intensity": "1.0", "azimuth": "0.0", "elevation": f"{math.radians(sun):.3f}"})
        ET.SubElement(weather_el, "Fog", {"visualRange": "100.0" if knobs.get("fog_density", 0.0) > 30 else "10000.0"})
        ET.SubElement(weather_el, "Precipitation", {"precipitationType": "rain" if precipitation > 0 else "dry", "intensity": f"{precipitation / 100.0:.2f}"})
        ET.SubElement(env, "RoadCondition", {"frictionScaleFactor": "0.7" if precipitation > 0 else "1.0"})

    def _init_private(self, actions: ET.Element, entity: PlacedEntity) -> None:
        private = ET.SubElement(actions, "Private", {"entityRef": entity.entity_name})
        teleport = ET.SubElement(ET.SubElement(private, "PrivateAction"), "TeleportAction")
        self._world_position(ET.SubElement(teleport, "Position"), entity.x, entity.y, entity.z, entity.yaw)
        speed = ET.SubElement(ET.SubElement(ET.SubElement(private, "PrivateAction"), "LongitudinalAction"), "SpeedAction")
        ET.SubElement(speed, "SpeedActionDynamics", {"dynamicsShape": "step", "value": "0.0", "dynamicsDimension": "time"})
        ET.SubElement(ET.SubElement(speed, "SpeedActionTarget"), "AbsoluteTargetSpeed", {"value": f"{entity.initial_speed_ms:.3f}"})

    def _route_maneuver(self, act_el: ET.Element, ego: PlacedEntity, route: list[dict[str, float]] | None) -> None:
        """Lane-following route along the grounded ego path, so the ego drives into the conflict.

        It is an Act event, not an Init action: ScenarioRunner keeps an Init route RUNNING until the ego reaches the
        last waypoint and the scenario cannot end before that, whereas the Act StopTrigger cancels an Act event.
        """
        if not route or len(route) < 2:
            return
        group = ET.SubElement(act_el, "ManeuverGroup", {"maximumExecutionCount": "1", "name": f"MG_{ego.entity_name}_route"})
        ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": ego.entity_name})
        event = ET.SubElement(ET.SubElement(group, "Maneuver", {"name": f"Maneuver_{ego.entity_name}_route"}), "Event",
                              {"name": f"Event_{ego.entity_name}_route", "priority": "overwrite"})
        private = ET.SubElement(ET.SubElement(event, "Action", {"name": "Action_ego_route"}), "PrivateAction")
        routing = ET.SubElement(ET.SubElement(private, "RoutingAction"), "AssignRouteAction")
        route_el = ET.SubElement(routing, "Route", {"name": "ego_route", "closed": "false"})
        for point in route:
            waypoint = ET.SubElement(route_el, "Waypoint", {"routeStrategy": "shortest"})
            self._world_position(ET.SubElement(waypoint, "Position"), point["x"], point["y"], ego.z, point["yaw"])
        start = ET.SubElement(ET.SubElement(ET.SubElement(event, "StartTrigger"), "ConditionGroup"), "Condition",
                              {"name": "EgoRouteStart", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(start, "ByValueCondition"), "SimulationTimeCondition", {"value": "0", "rule": "greaterThan"})

    @staticmethod
    def _maneuver(act_el: ET.Element, idx: int, actor: PlacedEntity, ego: PlacedEntity) -> None:
        ego_name = ego.entity_name
        group = ET.SubElement(act_el, "ManeuverGroup", {"maximumExecutionCount": "1", "name": f"MG_{actor.entity_name}"})
        ET.SubElement(ET.SubElement(group, "Actors", {"selectTriggeringEntities": "false"}), "EntityRef", {"entityRef": actor.entity_name})
        maneuver = ET.SubElement(group, "Maneuver", {"name": f"Maneuver_{actor.entity_name}"})
        event = ET.SubElement(maneuver, "Event", {"name": f"Event_{actor.entity_name}", "priority": "overwrite"})
        action = ET.SubElement(event, "Action", {"name": f"Action_{actor.trigger or 'cruise'}_{idx}"})
        private = ET.SubElement(action, "PrivateAction")
        trigger = actor.trigger
        if trigger in ("cut_in", "lane_departure"):
            change = ET.SubElement(ET.SubElement(private, "LateralAction"), "LaneChangeAction")
            ET.SubElement(change, "LaneChangeActionDynamics", {"dynamicsShape": "sinusoidal", "value": "25.0", "dynamicsDimension": "distance"})
            ET.SubElement(ET.SubElement(change, "LaneChangeTarget"), "RelativeTargetLane", {"entityRef": ego_name, "value": toward_ego_lane(actor, ego)})
        elif trigger in ("sudden_brake", "door_opening"):
            speed = ET.SubElement(ET.SubElement(private, "LongitudinalAction"), "SpeedAction")
            ET.SubElement(speed, "SpeedActionDynamics", {"dynamicsShape": "linear", "value": "7.5", "dynamicsDimension": "rate"})
            ET.SubElement(ET.SubElement(speed, "SpeedActionTarget"), "AbsoluteTargetSpeed", {"value": "0.0"})
        else:
            speed = ET.SubElement(ET.SubElement(private, "LongitudinalAction"), "SpeedAction")
            ET.SubElement(speed, "SpeedActionDynamics", {"dynamicsShape": "step", "value": "0.5", "dynamicsDimension": "time"})
            ET.SubElement(ET.SubElement(speed, "SpeedActionTarget"), "AbsoluteTargetSpeed", {"value": f"{max(1.5, actor.initial_speed_ms):.3f}"})

        condition = ET.SubElement(ET.SubElement(ET.SubElement(event, "StartTrigger"), "ConditionGroup"), "Condition",
                                  {"name": f"Trigger_{actor.entity_name}", "delay": "0", "conditionEdge": "rising"})
        by_entity = ET.SubElement(condition, "ByEntityCondition")
        ET.SubElement(ET.SubElement(by_entity, "TriggeringEntities", {"triggeringEntitiesRule": "any"}), "EntityRef", {"entityRef": ego_name})
        ET.SubElement(ET.SubElement(by_entity, "EntityCondition"), "RelativeDistanceCondition", {
            "entityRef": actor.entity_name,
            "relativeDistanceType": "cartesianDistance",
            "value": f"{actor.trigger_distance_m or 15.0}",
            "freespace": "false",
            "rule": "lessThan",
        })


# libxml2 schema parsing/validation is not safe to run concurrently from FastAPI's thread pool.
_SCHEMA_LOCK = threading.Lock()
_SCHEMA = None


def _schema():
    global _SCHEMA
    if _SCHEMA is None:
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        _SCHEMA = etree.XMLSchema(etree.parse(str(XSD_PATH), parser))
    return _SCHEMA


def validate_xosc(xml_text: str) -> dict[str, Any]:
    """Validate against the ASAM OpenSCENARIO 1.0 XSD shipped with ScenarioRunner 0.9.15."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        document = etree.fromstring(xml_text.encode("utf-8"), parser)
    except etree.XMLSyntaxError as exc:
        return {"schema_valid": False, "errors": [str(exc)], "schema": "OpenSCENARIO 1.0"}
    with _SCHEMA_LOCK:
        schema = _schema()
        valid = schema.validate(document)
        errors = [] if valid else [str(error) for error in schema.error_log][:20]
    return {"schema_valid": bool(valid), "errors": errors, "schema": "OpenSCENARIO 1.0"}
