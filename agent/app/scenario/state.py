from __future__ import annotations

from typing import Any, TypedDict


class ScenarioState(TypedDict, total=False):
    """LangGraph state. Every key a node reads or writes must be declared here: undeclared keys
    passed to `invoke()` are silently dropped by LangGraph."""

    prompt: str
    seed: int | None
    auto_repair: bool
    # GenerationConstraints from the structured form (None for a free-text prompt).
    constraints: dict[str, Any] | None

    intent_type: str
    regulation_answer: str | None
    retrieved_regulations: list[dict[str, Any]]
    retrieved_project_documents: list[dict[str, Any]]
    retrieved_components: list[dict[str, Any]]
    retrieved_history: list[dict[str, Any]]

    interpretation: dict[str, Any] | None
    spatial_step: dict[str, Any] | None
    ego_step: dict[str, Any] | None
    actors_step: dict[str, Any] | None
    triggers_step: dict[str, Any] | None
    weather_step: dict[str, Any] | None

    scenario_ir: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    repair_attempts: int
    max_repair_attempts: int

    grounding: dict[str, Any] | None
    xosc: str | None
    xosc_validation: dict[str, Any] | None

    warnings: list[str]
    status: str
    error_code: str | None
    error_message: str | None
