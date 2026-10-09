"""Inputs and outputs of the motorcycle cut-in domain logic.

Prompt constraints contain only what the user specified. Missing values stay
unset so a later sampler can draw from supported map and weather choices.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ChoiceSource = Literal["user", "random", "derived"]


class PromptConstraints(BaseModel):
    scenario_type: Literal["motorcycle_cut_in"] = "motorcycle_cut_in"
    location_tags: list[str] = Field(default_factory=list)
    weather_conditions: list[str] = Field(default_factory=list)
    lighting: str | None = None
    road_surface: str | None = None
    ego_speed_kmh: float | None = Field(default=None, ge=0)
    motorcycle_speed_kmh: float | None = Field(default=None, ge=0)
    ambiguities: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)


class EnvironmentSelection(BaseModel):
    """The supported environment profile chosen for one variant."""

    profile_id: str = Field(min_length=1)
    weather_conditions: list[str] = Field(default_factory=list)
    weather_preset: str | None = None
    time_of_day_hour: float = Field(ge=0, lt=24)
    road_surface: str = Field(min_length=1)
    friction_scale_factor: float = Field(gt=0)
    sources: dict[str, ChoiceSource] = Field(default_factory=dict)


class SampledContext(BaseModel):
    """Choices fixed before the LLM proposes the maneuver."""

    site_id: str = Field(min_length=1)
    ego_blueprint_id: str = Field(min_length=1)
    motorcycle_blueprint_id: str = Field(min_length=1)
    ego_speed_kmh: float = Field(gt=0)
    motorcycle_speed_kmh: float = Field(gt=0)
    environment: EnvironmentSelection
    sources: dict[str, ChoiceSource] = Field(default_factory=dict)


class CutInPlan(SampledContext):
    """Maneuver parameters; exact CARLA positions come from ``site_id``."""

    # Signed longitudinal offset from ego's starting point; + means ahead.
    motorcycle_start_offset_m: float
    trigger_time_s: float = Field(ge=0)
    lane_change_duration_s: float = Field(gt=0)
    desired_lead_gap_m: float = Field(gt=0)


class ValidationIssue(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    field: str | None = None
    recoverable: bool = False


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
