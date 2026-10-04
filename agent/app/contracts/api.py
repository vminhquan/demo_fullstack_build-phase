from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.catalog_v1 import CatalogV1
from app.scenario.spec import Adjustment, ChosenValues, GenerationConstraints, ScenarioSpec


class Usage(BaseModel):
    """LLM cost of one request (0 tokens in deterministic mode)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    catalog: CatalogV1
    auto_repair: bool = True
    # True forces the deterministic keyword generator even when an OpenAI key is configured.
    offline_mode: bool = False
    # Picks among equally good ego placements so "regenerate" can vary the layout.
    seed: int | None = Field(default=None, ge=0)
    # From POST /v1/prompts/refine: keeps the generated IR inside the structured form's ranges.
    constraints: GenerationConstraints | None = None


class RefineRequest(BaseModel):
    spec: ScenarioSpec
    catalog: CatalogV1
    offline_mode: bool = False
    # Picks the weather when several are selected (same seed as the following generation).
    seed: int | None = Field(default=None, ge=0)


class RefineResponse(BaseModel):
    refined_prompt: str
    values: ChosenValues
    rationale: str
    refine_mode: Literal["llm", "deterministic"]
    model: str | None
    spec: ScenarioSpec
    constraints: GenerationConstraints
    adjustments: list[Adjustment]
    road_type: str
    weather: str
    warnings: list[str]
    usage: Usage = Usage()


class CatalogRef(BaseModel):
    map_name: str
    carla_version: str
    content_hash: str | None


class GenerateResponse(BaseModel):
    status: Literal["COMPLETED"] = "COMPLETED"
    generation_mode: Literal["llm", "deterministic"]
    model: str | None
    catalog: CatalogRef
    scenario_ir: dict[str, Any]
    interpretation: dict[str, Any]
    validation: dict[str, Any]
    threat_score: dict[str, Any]
    grounding: dict[str, Any]
    xosc: str
    xosc_sha256: str
    xosc_validation: dict[str, Any]
    retrieved_regulations: list[str]
    retrieved_history: list[str]
    warnings: list[str]
    usage: Usage = Usage()


class TitleRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4000)


class TitleResponse(BaseModel):
    title: str
    mode: Literal["llm", "fallback"]
    usage: Usage
