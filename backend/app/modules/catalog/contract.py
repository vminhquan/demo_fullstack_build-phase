"""catalog.v1 / catalog.v2 shared with Bridge and agent2 catalog snapshots.

All poses use CARLA world coordinates: metres, left-handed (x forward, y right), yaw in degrees.
catalog.v2 adds lane rules, corridor facts, road speeds, junctions, landmarks, traffic lights, crosswalks, topology,
vehicle sizes, weather values and the OpenDRIVE text. Every v2 field is optional: a v1
document is still valid, and `extraction_errors` lists the parts the user's CARLA could not provide.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CATALOG_FORMAT = "scenario-forge.catalog.v1"
CATALOG_FORMAT_V2 = "scenario-forge.catalog.v2"


class Pose(BaseModel):
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0


class Point(BaseModel):
    x: float
    y: float
    z: float = 0.0


class LaneRef(BaseModel):
    road_id: int
    section_id: int
    lane_id: int


class WaypointRef(LaneRef):
    s: float


class WaypointPose(WaypointRef):
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0


class LaneMarking(BaseModel):
    type: str  # Broken, Solid, SolidSolid, NONE...
    color: str | None = None
    lane_change: str | None = None


class NeighborLane(WaypointRef):
    # Driving, Sidewalk, Shoulder, Parking...; a lane_id of the other sign runs the other way.
    lane_type: str | None = None


class LaneWaypoint(Pose):
    road_id: int
    section_id: int = 0
    lane_id: int
    s: float = 0.0
    lane_width: float = Field(default=3.5, gt=0)
    is_junction: bool = False
    lane_type: str = "Driving"
    # catalog.v2 lane rules
    junction_id: int | None = None
    lane_change: str | None = None  # what this lane allows: NONE, Left, Right, Both
    left_marking: LaneMarking | None = None
    right_marking: LaneMarking | None = None
    left_lane: NeighborLane | None = None
    right_lane: NeighborLane | None = None


class Blueprint(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    # CARLA "base_type" attribute: car, truck, van, bus, motorcycle, bicycle (empty for some models).
    base_type: str | None = None
    number_of_wheels: int | None = None
    # catalog.v2: bounding box of the vehicle blueprint
    length_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)


class LaneWidths(BaseModel):
    ego: float
    motorcycle: float


class CatalogCutInSite(BaseModel):
    site_id: str = Field(min_length=1)
    ego_lane: LaneRef
    motorcycle_lane: LaneRef
    ego_anchor: Pose
    motorcycle_anchor: Pose
    available_length_m: float = Field(gt=0)
    location_tags: list[str] = Field(default_factory=list)
    verification_source: Literal["carla_topology", "manual"]
    ego_s: float | None = Field(default=None, ge=0)
    motorcycle_s: float | None = Field(default=None, ge=0)
    s_direction: Literal[-1, 1] | None = None
    target_side: Literal["left", "right"] | None = None
    # catalog.v2 corridor facts (location_tags still describe the first 60 m)
    upstream_length_m: float | None = Field(default=None, ge=0)
    lane_change_allowed_throughout: bool | None = None
    marking_between: list[str] | None = None
    max_heading_change_deg: float | None = None
    first_junction_m: float | None = None
    ends_at: str | None = None  # max_length, lane_pair_ends, branch_or_end, discontinuity, step_limit
    lane_width_m: LaneWidths | None = None
    speed_limit_kmh: float | None = None

    @model_validator(mode="after")
    def distinct_lanes(self) -> CatalogCutInSite:
        if self.ego_lane == self.motorcycle_lane:
            raise ValueError("Ego and motorcycle must start in different lanes")
        return self


class RoadSpeed(BaseModel):
    road_id: int
    from_s: float
    max_kmh: float = Field(gt=0)


class LaneLink(BaseModel):
    from_lane: int
    to_lane: int


class JunctionConnection(BaseModel):
    incoming_road_id: int
    connecting_road_id: int
    contact_point: str | None = None
    lane_links: list[LaneLink] = Field(default_factory=list)


class JunctionLanePath(BaseModel):
    entry: WaypointPose
    exit: WaypointPose


class Junction(BaseModel):
    junction_id: int
    center: Point | None = None
    extent: Point | None = None
    incoming_road_ids: list[int] = Field(default_factory=list)
    incoming_road_count: int = 0
    four_way_candidate: bool = False  # four incoming roads: a hint, not proof of a four-way crossing
    connections: list[JunctionConnection] = Field(default_factory=list)
    lane_paths: list[JunctionLanePath] = Field(default_factory=list)


class Landmark(BaseModel):
    id: str
    type: str  # 1000001 traffic light, 206 stop, 205 yield, 274 speed limit
    sub_type: str | None = None
    name: str | None = None
    road_id: int
    s: float
    value: float | None = None
    unit: str | None = None
    text: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float | None = None
    affected_lanes: list[list[int]] = Field(default_factory=list)  # [from_lane, to_lane] ranges


class TrafficLight(BaseModel):
    actor_id: int  # changes when the map is reloaded; opendrive_id is the stable key
    opendrive_id: str | None = None
    x: float
    y: float
    z: float = 0.0
    pole_index: int | None = None
    group_actor_ids: list[int] = Field(default_factory=list)
    stop_waypoints: list[WaypointPose] = Field(default_factory=list)
    affected_lanes: list[LaneRef] = Field(default_factory=list)
    green_s: float | None = None
    yellow_s: float | None = None
    red_s: float | None = None


class Crosswalk(BaseModel):
    polygon: list[Point] = Field(min_length=3)
    center: Point
    nearest_driving_lane: LaneRef | None = None


class TopologyEdge(BaseModel):
    start: WaypointRef
    end: WaypointRef


class ExtractionError(BaseModel):
    part: str
    error: str


class CatalogV1(BaseModel):
    """A catalog.v1 or catalog.v2 document (the name predates v2)."""

    format: Literal["scenario-forge.catalog.v1", "scenario-forge.catalog.v2"] = CATALOG_FORMAT
    carla_version: str = Field(min_length=1, max_length=64)
    # Short map name as CARLA loads it, e.g. "Town10HD_Opt" (not "Carla/Maps/Town10HD_Opt").
    map_name: str = Field(min_length=1, max_length=120)
    available_maps: list[str] = Field(default_factory=list)
    opendrive_hash: str | None = None
    vehicles: list[Blueprint] = Field(min_length=1)
    walkers: list[Blueprint] = Field(default_factory=list)
    spawn_points: list[Pose] = Field(min_length=1)
    waypoints: list[LaneWaypoint] = Field(min_length=1)
    weather_presets: list[str] = Field(default_factory=list)
    # None = old snapshot without a site index; [] = checked, no suitable site found.
    cut_in_sites: list[CatalogCutInSite] | None = None
    # Set by the Backend when it stores the snapshot; echoed into generation results for traceability.
    content_hash: str | None = None
    # catalog.v2 map facts. opendrive_xml is stored apart from the JSON document (see ingest_snapshot).
    opendrive_xml: str | None = None
    weather_parameters: dict[str, dict[str, float]] | None = None
    road_speeds: list[RoadSpeed] | None = None
    junctions: list[Junction] | None = None
    landmarks: list[Landmark] | None = None
    traffic_lights: list[TrafficLight] | None = None
    crosswalks: list[Crosswalk] | None = None
    topology: list[TopologyEdge] | None = None
    extraction_errors: list[ExtractionError] | None = None
