"""CARLA catalog input and verified cut-in site contracts.

The catalog shape matches the Bridge's ``scenario-forge.catalog.v1`` payload. A ``catalog.v2`` document is
accepted too: its extra facts (lane rules, junctions, signals...) are ignored here until the Agent uses them.
Sampled waypoints alone do not prove lane adjacency, so ``CutInSite`` is a
separate, verified result produced by a later site-finding step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Pose(BaseModel):
    """CARLA world coordinates: metres and yaw in degrees."""

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
    base_type: str | None = None
    number_of_wheels: int | None = None


class LaneRef(BaseModel):
    road_id: int
    section_id: int
    lane_id: int


class CatalogCutInSite(BaseModel):
    """A site verified at CARLA sync time, before a snapshot ID exists."""

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

    @model_validator(mode="after")
    def distinct_lanes(self) -> CatalogCutInSite:
        if self.ego_lane == self.motorcycle_lane:
            raise ValueError("Ego and motorcycle must start in different lanes")
        return self


class CatalogV1(BaseModel):
    format: Literal["scenario-forge.catalog.v1", "scenario-forge.catalog.v2"] = "scenario-forge.catalog.v1"
    carla_version: str = Field(min_length=1, max_length=64)
    map_name: str = Field(min_length=1, max_length=120)
    available_maps: list[str] = Field(default_factory=list)
    opendrive_hash: str | None = None
    vehicles: list[Blueprint] = Field(min_length=1)
    walkers: list[Blueprint] = Field(default_factory=list)
    spawn_points: list[Pose] = Field(min_length=1)
    waypoints: list[LaneWaypoint] = Field(min_length=1)
    weather_presets: list[str] = Field(default_factory=list)
    # None means a legacy catalog with no site index; [] means the Bridge checked and found none.
    cut_in_sites: list[CatalogCutInSite] | None = None
    content_hash: str | None = None


class SnapshotRef(BaseModel):
    """Immutable identity of the catalog selected by the Backend."""

    snapshot_id: int = Field(gt=0)
    map_name: str = Field(min_length=1, max_length=120)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SelectedSnapshot(BaseModel):
    ref: SnapshotRef
    catalog: CatalogV1

    @model_validator(mode="after")
    def matches_catalog(self) -> SelectedSnapshot:
        if self.ref.map_name != self.catalog.map_name:
            raise ValueError("Snapshot map_name does not match the catalog")
        if self.catalog.content_hash and self.ref.content_hash != self.catalog.content_hash:
            raise ValueError("Snapshot content_hash does not match the catalog")
        return self


class CutInSite(CatalogCutInSite):
    """A verified pair of adjacent same-direction driving lanes.

    Its actual topology proof belongs to the site finder, not this schema.
    ``available_length_m`` is the continuous usable distance for the maneuver.
    """

    snapshot: SnapshotRef
