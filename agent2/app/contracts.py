"""API contracts and LangGraph state for the scenario generator.

The request carries complete catalogs for validation and XOSC export. GraphState keeps the chat with the LLM and
small results; catalogs reach the nodes through GraphContext so checkpointing does not copy waypoint arrays.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from typing_extensions import NotRequired

from app.catalog.models import SelectedSnapshot, SnapshotRef
from app.scenario.spec import ScenarioPlan
from pydantic import BaseModel, Field, model_validator

GenerationStatus = Literal["completed", "partial", "failed", "needs_clarification"]


class SaveTarget(BaseModel):
    """Who the stored generation belongs to; set by the Backend and sent back unchanged by the save node."""

    project_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    prompt: str = Field(min_length=1)
    request_hash: str | None = None


class GenerationRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=3, max_length=4000)
    selected_snapshots: list[SelectedSnapshot] = Field(min_length=1, max_length=20)
    # One run makes one scenario; the Backend's Builder runs the graph once per variant (10 per session).
    target_count: int = Field(default=1, ge=1, le=1)
    seed: int | None = Field(default=None, ge=0)
    # Submissions checked per run; the simulation feedback usually needs a few rounds.
    max_proposal_attempts: int = Field(default=5, ge=1, le=5)
    ego_blueprint_id: str | None = None
    weather_preset: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    lighting: str | None = None
    # Kind of road user the form locked for the one causing the incident (motorcycle, car, truck, bicycle...).
    actor_type: str | None = None
    # Unset: the scenario is only returned, not stored (tests, local runs).
    save_to: SaveTarget | None = None

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
    # Ego start "road:lane:s".
    site_id: str = Field(min_length=1)
    plan: ScenarioPlan
    xosc: str = Field(min_length=1)
    xosc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Id of the Backend ScenarioGeneration row the save node wrote; None when the request had no save_to.
    generation_id: int | None = None

    @model_validator(mode="after")
    def matches_plan(self) -> GeneratedScenario:
        if self.site_id != self.plan.start_id:
            raise ValueError("Generated site_id does not match the plan")
        return self


class GenerationResponse(BaseModel):
    session_id: str
    status: GenerationStatus
    target_count: int = Field(ge=1)
    seed: int = Field(ge=0)
    model_name: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
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
    """Per-run state. Nodes update only the keys they produce."""

    session_id: str
    seed: int
    # OpenAI chat messages: system, user, then assistant tool calls and their tool results.
    messages: list[dict[str, Any]]
    llm_turns: int
    usage: dict[str, int]
    submit_attempts: int
    # The submit_scenario call waiting for build_xosc: {"id": ..., "arguments": "<json>"}.
    pending_submit: NotRequired[dict[str, str] | None]
    scenario: NotRequired[GeneratedScenario | None]
    map_failures: list[MapFailure]
    status: NotRequired[GenerationStatus]


class GraphContext(TypedDict):
    """Runtime-only data, available to nodes but excluded from checkpoints."""

    selected_snapshots: list[SelectedSnapshot]
    request: GenerationRequest
    llm: Any
    # Backend endpoints (app.llm.tools.CatalogApi): the catalog reads behind the tools, and the save.
    catalog_api: Any
    # Lane graph of each selected map (app.scenario.lanes.LaneGraph), built once per run from its waypoints.
    lane_graphs: Any
