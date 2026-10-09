"""API contracts and LangGraph state for the new scenario generator.

The request carries complete catalogs for validation. GraphState keeps only
their immutable references; nodes receive catalogs through GraphContext so
checkpointing does not duplicate large waypoint arrays.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from app.catalog.models import CutInSite, SelectedSnapshot, SnapshotRef
from app.cut_in.model import CutInPlan, PromptConstraints, ValidationResult
from pydantic import BaseModel, Field, model_validator

GenerationStatus = Literal["completed", "partial", "failed", "needs_clarification"]


class GenerationRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=3, max_length=4000)
    selected_snapshots: list[SelectedSnapshot] = Field(min_length=1, max_length=20)
    target_count: int = Field(ge=1, le=100)
    seed: int | None = Field(default=None, ge=0)
    max_proposal_attempts: int = Field(default=3, ge=1, le=3)
    ego_blueprint_id: str | None = None
    weather_preset: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    lighting: str | None = None

    @model_validator(mode="after")
    def unique_selected_maps(self) -> GenerationRequest:
        refs = [selected.ref for selected in self.selected_snapshots]
        if len({ref.map_name for ref in refs}) != len(refs):
            raise ValueError("Each map may be selected only once")
        if len({ref.snapshot_id for ref in refs}) != len(refs):
            raise ValueError("Each snapshot may be selected only once")
        return self


class MapFailure(BaseModel):
    snapshot: SnapshotRef
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class GeneratedScenario(BaseModel):
    scenario_id: str = Field(min_length=1)
    variant_no: int = Field(ge=1)
    snapshot: SnapshotRef
    site_id: str = Field(min_length=1)
    plan: CutInPlan
    validation: ValidationResult
    xosc: str = Field(min_length=1)
    xosc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def matches_plan(self) -> GeneratedScenario:
        if self.site_id != self.plan.site_id:
            raise ValueError("Generated site_id does not match the plan")
        if not self.validation.valid:
            raise ValueError("A generated XOSC requires a valid cut-in plan")
        return self


class GenerationResponse(BaseModel):
    session_id: str
    status: GenerationStatus
    target_count: int = Field(ge=1)
    seed: int = Field(ge=0)
    model_name: str | None = None
    scenarios: list[GeneratedScenario] = Field(default_factory=list)
    map_failures: list[MapFailure] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def matches_status(self) -> GenerationResponse:
        count = len(self.scenarios)
        if count > self.target_count:
            raise ValueError("Generated more scenarios than requested")
        if self.status == "completed" and count != self.target_count:
            raise ValueError("A completed session must meet target_count")
        if self.status == "partial" and not 0 < count < self.target_count:
            raise ValueError("A partial session must contain fewer than target_count scenarios")
        if self.status in {"failed", "needs_clarification"} and count:
            raise ValueError("A failed session cannot contain generated scenarios")
        if self.status == "needs_clarification" and not self.clarification_questions:
            raise ValueError("Clarification questions are required for needs_clarification")
        return self


class GraphState(TypedDict):
    """Per-session state. Nodes update only the keys they produce."""

    session_id: str
    prompt: str
    snapshot_refs: list[SnapshotRef]
    target_count: int
    seed: int
    max_proposal_attempts: int
    constraints: NotRequired[PromptConstraints]
    sites: NotRequired[list[CutInSite]]
    sampled_variants: NotRequired[list[dict[str, Any]]]
    variant_index: NotRequired[int]
    current_plan: NotRequired[CutInPlan | None]
    validation: NotRequired[ValidationResult | None]
    feedback: NotRequired[list[str]]
    scenarios: NotRequired[list[GeneratedScenario]]
    map_failures: NotRequired[list[MapFailure]]
    clarification_questions: NotRequired[list[str]]
    proposal_attempts: NotRequired[int]
    status: NotRequired[GenerationStatus]


class GraphContext(TypedDict):
    """Runtime-only data, available to nodes but excluded from checkpoints."""

    selected_snapshots: list[SelectedSnapshot]
    request: GenerationRequest
    llm: Any
    # Backend catalog reads for tool calls (app.llm.tools.CatalogApi); None when BACKEND_URL is not set.
    catalog_api: Any
