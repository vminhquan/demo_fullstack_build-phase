from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RoadCategory = Literal["intersection", "straight", "curve", "highway"]
Weather = Literal["clear", "rain", "heavy_rain", "fog"]
Lighting = Literal["day", "dusk", "night"]
ScenarioKind = Literal["cut_in", "sudden_brake", "crossing", "oncoming_lane_departure", "door_opening"]
Objective = Literal["critical", "boundary", "nominal"]


class NumberRange(BaseModel):
    min: float = Field(ge=0, le=200)
    max: float = Field(ge=0, le=200)

    @model_validator(mode="after")
    def _ordered(self) -> "NumberRange":
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class OddDeclaration(BaseModel):
    """What the system under test is designed for. Every list is the allowed set for that dimension."""

    catalog_snapshot_ids: list[int] = Field(min_length=1, max_length=20)
    road_types: list[RoadCategory] = Field(min_length=1)
    weather: list[Weather] = Field(min_length=1)
    lighting: list[Lighting] = Field(min_length=1)
    adversary_types: list[str] = Field(min_length=1, max_length=10)
    ego_vehicles: list[str] = Field(min_length=1, max_length=20)
    ego_speed_kmh: NumberRange = NumberRange(min=10, max=60)
    actor_speed_kmh: NumberRange = NumberRange(min=0, max=60)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _dedupe(self) -> "OddDeclaration":
        for name in ("catalog_snapshot_ids", "road_types", "weather", "lighting", "adversary_types", "ego_vehicles"):
            setattr(self, name, list(dict.fromkeys(getattr(self, name))))
        return self


class MapCapability(BaseModel):
    """What one CARLA snapshot can host, derived from its lane data and blueprints."""

    snapshot_id: int
    map_name: str
    carla_version: str
    origin: str
    road_types: list[RoadCategory]
    has_oncoming_lane: bool
    adversary_types: list[str]
    ego_vehicles: list[str]
    error: str | None = None


class OddGap(BaseModel):
    dimension: str
    value: str
    reason: str


class OddResponse(BaseModel):
    declaration: OddDeclaration | None
    updated_at: datetime | None
    updated_by: int | None
    capabilities: list[MapCapability]
    gaps: list[OddGap]


class OddCheckResponse(BaseModel):
    gaps: list[OddGap]


class PlanForm(BaseModel):
    """Structured form with multi-valued ODD dimensions; the planner spreads N cases over them."""

    goal: str = Field(default="", max_length=2000)
    scenario_type: ScenarioKind = "cut_in"
    direction: Literal["left", "right"] = "left"
    objective: Objective = "critical"
    road_types: list[RoadCategory] = Field(min_length=1)
    weather: list[Weather] = Field(min_length=1)
    lighting: list[Lighting] = Field(min_length=1)
    adversary_types: list[str] = Field(min_length=1)
    ego_speed_kmh: NumberRange
    actor_speed_kmh: NumberRange
    initial_gap_m: NumberRange
    trigger_distance_m: NumberRange
    ego_blueprint: str | None = None


MAX_PLAN_CASES = 20


class PlanRequest(BaseModel):
    form: PlanForm
    # None = automatic: the fewest cases that cover every feasible pair of ODD values (capped at MAX_PLAN_CASES).
    count: int | None = Field(default=None, ge=1, le=MAX_PLAN_CASES)


class PlannedCase(BaseModel):
    index: int
    catalog_snapshot_id: int
    map_name: str
    cell: dict[str, str]
    # Single-valued agent ScenarioSpec (agent/app/scenario/spec.py) for POST /scenario-generations/refine.
    spec: dict[str, Any]


class PlanResponse(BaseModel):
    cases: list[PlannedCase]
    auto: bool = False
    # value -> number of cases, per dimension
    coverage: dict[str, dict[str, int]]
    pairs_total: int
    pairs_covered: int
    uncovered_pairs: list[str]
    infeasible: list[OddGap]
    outside_odd: list[OddGap]
