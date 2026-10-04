"""catalog.v1 — Backend copy of agent/app/contracts/catalog_v1.py (keep both identical).

All poses use CARLA world coordinates: metres, left-handed (x forward, y right), yaw in degrees.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CATALOG_FORMAT = "scenario-forge.catalog.v1"


class Pose(BaseModel):
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0


class LaneWaypoint(Pose):
    road_id: int
    section_id: int = 0
    lane_id: int
    s: float = 0.0
    lane_width: float = Field(default=3.5, gt=0)
    is_junction: bool = False
    lane_type: str = "Driving"


class Blueprint(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    # CARLA "base_type" attribute: car, truck, van, bus, motorcycle, bicycle (empty for some models).
    base_type: str | None = None
    number_of_wheels: int | None = None


class CatalogV1(BaseModel):
    format: Literal["scenario-forge.catalog.v1"] = CATALOG_FORMAT
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
    # Set by the Backend when it stores the snapshot; echoed into generation results for traceability.
    content_hash: str | None = None
