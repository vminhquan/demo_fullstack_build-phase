"""Turn a structured form into a concrete, generation-ready prompt (LLM, with a deterministic fallback)."""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import chat_model
from app.scenario.grounding import CatalogGrounder
from app.scenario.spec import (
    ADVERSARY_VI,
    LIGHTING_VI,
    ROAD_VI,
    WEATHER_VI,
    ChosenValues,
    NormalizedSpec,
    default_values,
    describe,
    fit_values,
)
from app.scenario.validator import ScenarioGuardrailValidator

log = logging.getLogger("scenario_forge.agent.refiner")

OBJECTIVE_TEXT = {
    "critical": "Find a DANGEROUS case: choose values near the risky end of each range (fast ego, short gaps, late trigger) "
                "so the expected outcome is a near-miss or collision, but keep it physically fair.",
    "boundary": "Find the PASS/FAIL BOUNDARY: choose values where a competent driver barely avoids the conflict.",
    "nominal": "Describe a NOMINAL, comfortably avoidable case: values toward the calm end of each range.",
}
SCENARIO_TEXT = {
    "cut_in": "the adversary cuts in from the {side} adjacent lane in front of the ego",
    "sudden_brake": "the adversary drives ahead in the ego lane and brakes suddenly",
    "crossing": "the adversary crosses the ego path from the {side}",
    "oncoming_lane_departure": "an oncoming adversary drifts into the ego lane",
    "door_opening": "a parked adversary on the right opens its door in front of the ego",
}


class RefinedPrompt(BaseModel):
    """LLM output for the refine step."""

    prompt: str = Field(description="2-4 Vietnamese sentences describing ONE concrete scenario with every number stated")
    values: ChosenValues = Field(description="The concrete numbers used in the prompt, each inside its range")
    rationale: str = Field(default="", description="One Vietnamese sentence: why these values serve the test objective")


class RefineResult(BaseModel):
    prompt: str
    values: ChosenValues
    rationale: str
    mode: str
    model: str | None
    warnings: list[str]


def _instructions(normalized: NormalizedSpec, grounder: CatalogGrounder) -> str:
    spec = normalized.spec
    ttc = ScenarioGuardrailValidator.MIN_FAIR_TTC_SECONDS
    return (
        "You refine a structured ADAS test form into one concrete driving-scenario description for a CARLA scenario generator.\n"
        "Rules:\n"
        "- Write the prompt in Vietnamese, 2-4 sentences, no lists. Mention the road layout, weather, lighting, ego speed, "
        "the adversary type, where it starts relative to the ego (distance), its speed, what it does and at which distance it triggers.\n"
        "- Call the vehicle under test \"ego\" (never \"my car\"). Write in the third person.\n"
        "- Use exactly ONE adversary. Do not add other traffic participants.\n"
        "- Every number must lie inside its range. The trigger distance must be <= the initial distance.\n"
        f"- Fairness: trigger distance / closing speed must be >= {ttc} s (ego reaction time).\n"
        "- Do not invent a different map, road type, weather, adversary or behaviour than the form says.\n"
        "- If the engineer's goal text conflicts with the form, the form wins; mention the goal only as intent.\n"
        f"\nObjective: {OBJECTIVE_TEXT[spec.objective]}\n"
        f"\nMap context: {grounder.profile().prompt_text()}\n"
        "\nForm (already normalized to what the map and guardrails support):\n"
        f"- Road layout: {normalized.road_type} ({ROAD_VI.get(normalized.road_type, '')})\n"
        f"- Weather: {normalized.chosen_weather} ({WEATHER_VI[normalized.chosen_weather]}); lighting: {spec.lighting} ({LIGHTING_VI[spec.lighting]})\n"
        f"- Adversary: {spec.adversary_type} ({ADVERSARY_VI[spec.adversary_type]}); behaviour: "
        f"{SCENARIO_TEXT[spec.scenario_type].format(side=spec.direction)}\n"
        f"- Ego speed range: {spec.ego_speed_kmh.label('km/h')}\n"
        f"- Adversary speed range: {spec.actor_speed_kmh.label('km/h')}\n"
        f"- Initial distance range: {spec.initial_gap_m.label('m')}\n"
        f"- Trigger distance range: {spec.trigger_distance_m.label('m')}\n"
        f"- Engineer's goal: {spec.goal.strip() or '(none)'}\n"
    )


def refine(normalized: NormalizedSpec, grounder: CatalogGrounder, *, offline_mode: bool = False) -> RefineResult:
    settings = get_settings()
    fallback = default_values(normalized.spec)
    if offline_mode or not settings.llm_enabled:
        return RefineResult(prompt=describe(normalized, fallback), values=fallback, rationale="", mode="deterministic", model=None, warnings=[])
    try:
        raw: RefinedPrompt = chat_model().with_structured_output(RefinedPrompt, method="function_calling").invoke(_instructions(normalized, grounder))
    except Exception as exc:  # LLM/network failure: still give the user a usable prompt.
        log.warning("Prompt refinement failed: %s", type(exc).__name__)
        return RefineResult(prompt=describe(normalized, fallback), values=fallback, rationale="", mode="deterministic", model=None,
                            warnings=[f"LLM không phản hồi ({type(exc).__name__}); đã dùng mô tả dựng sẵn từ form."])
    values = fit_values(normalized.spec, raw.values)
    warnings = []
    prompt = raw.prompt.strip()
    if values != raw.values:
        # The LLM's numbers left the approved ranges: rebuild the text from the corrected values.
        warnings.append("LLM chọn số ngoài dải hoặc kích hoạt quá muộn; đã chỉnh lại và dựng lại mô tả.")
        prompt = describe(normalized, values)
    return RefineResult(prompt=prompt, values=values, rationale=raw.rationale.strip(), mode="llm", model=settings.model_name, warnings=warnings)
