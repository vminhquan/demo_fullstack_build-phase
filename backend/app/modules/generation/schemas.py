from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.modules.catalog.schemas import CatalogSnapshotResponse
from app.modules.testcase.schemas import VersionResponse
from app.shared.infrastructure.models import DangerLevel, GenerationStatus


class GenerationCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    # DEFAULT = shipped CARLA data; PROJECT = data synced from the user's CARLA (Worker) or imported by an admin.
    catalog_source: Literal["DEFAULT", "PROJECT"] = "DEFAULT"
    catalog_snapshot_id: int | None = None
    map_name: str | None = Field(default=None, max_length=120)
    auto_repair: bool = True
    seed: int | None = Field(default=None, ge=0)
    # Structured form: the form as submitted and the constraints returned by POST /scenario-generations/refine.
    spec: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    # The 5 metadata from /test-cases/new: picks the CARLA data of `map_code` and constrains the result.
    metadata: "GenerationMetadata | None" = None
    # Reuse the result of an identical earlier request instead of calling the LLM again.
    use_cache: bool = True


class GenerationMetadata(BaseModel):
    """The map is required; each other value is optional and, when left out, chosen by the Agent."""

    map_code: str = Field(min_length=1, max_length=120)
    ego_vehicle_code: str | None = Field(default=None, min_length=1, max_length=255)
    adversary_type: str | None = Field(default=None, min_length=1, max_length=120)
    environment_code: str | None = Field(default=None, min_length=1, max_length=120)


GenerationCreate.model_rebuild()


class GenerationRefine(BaseModel):
    """Structured form (ODD + parameter ranges); validated by the Agent (agent/app/scenario/spec.py)."""

    spec: dict[str, Any]
    catalog_source: Literal["DEFAULT", "PROJECT"] = "DEFAULT"
    catalog_snapshot_id: int | None = None
    seed: int | None = Field(default=None, ge=0)


class RefineResponse(BaseModel):
    catalog: CatalogSnapshotResponse
    refined_prompt: str
    values: dict[str, float]
    rationale: str
    refine_mode: str
    model: str | None
    spec: dict[str, Any]
    constraints: dict[str, Any]
    adjustments: list[dict[str, str]]
    road_type: str
    weather: str
    warnings: list[str]
    usage: dict[str, int] = Field(default_factory=dict)


class SuggestedVersion(BaseModel):
    map_code: str
    ego_vehicle_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    tag_names: list[str]


class GenerationResponse(BaseModel):
    id: int
    status: GenerationStatus
    prompt: str
    catalog: CatalogSnapshotResponse
    generation_mode: str
    model: str | None
    scenario_ir: dict[str, Any]
    # {"spec", "constraints"} when generated from the structured form.
    form: dict[str, Any] | None = None
    cache_hit: bool = False
    usage: dict[str, int] = Field(default_factory=dict)
    interpretation: dict[str, Any]
    validation: dict[str, Any]
    threat_score: dict[str, Any]
    grounding: dict[str, Any]
    xosc_validation: dict[str, Any]
    retrieved_regulations: list[str]
    warnings: list[str]
    xosc: str
    xosc_sha256: str
    suggested_version: SuggestedVersion
    accepted_version_id: int | None
    created_by: int
    created_at: datetime


class GenerationAccept(BaseModel):
    """Save the generated scenario as a DRAFT version: a new test case, or the next version of an existing one."""

    test_case_id: int | None = None
    # Attach to an existing DRAFT version instead (e.g. a version that has no .xosc yet).
    version_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    map_code: str = Field(min_length=1, max_length=120)
    ego_vehicle_code: str = Field(min_length=1, max_length=255)
    adversary_type: str = Field(min_length=1, max_length=120)
    environment_code: str = Field(min_length=1, max_length=120)
    danger_level: DangerLevel
    tag_names: list[str] = Field(default_factory=list)
    change_note: str | None = None

    @model_validator(mode="after")
    def _title_for_new_case(self) -> "GenerationAccept":
        if self.test_case_id is None and self.version_id is None and not self.title:
            raise ValueError("title is required when creating a new test case")
        return self


class GenerationAcceptResponse(BaseModel):
    generation_id: int
    test_case_id: int
    version: VersionResponse
