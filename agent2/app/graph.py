"""LangGraph workflow for catalog-grounded motorcycle cut-in generation.

Only prompt interpretation and maneuver proposal call the LLM. Every map,
vehicle, environment and XOSC decision is delegated to deterministic Python.
The full catalogs and LLM client live in runtime context, outside graph state.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, cast

from app.catalog.find_sites import find_cut_in_sites
from app.contracts import (
    GeneratedScenario,
    GenerationRequest,
    GraphContext,
    GraphState,
    MapFailure,
)
from app.cut_in.model import CutInPlan, PromptConstraints, ValidationResult
from app.cut_in.sample import SampledVariant, SamplingError, sample_variants
from app.cut_in.validate import validate_cut_in
from app.cut_in.xosc import XoscExportError, render_xosc
from app.llm.client import LLMCallError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime


class GeneratorLLM(Protocol):
    model_name: str

    def extract_constraints(self, prompt: str) -> PromptConstraints: ...

    def propose_maneuver(self, prompt: str, variant: SampledVariant, *, feedback: list[str] | None = None) -> CutInPlan: ...


def initial_state(request: GenerationRequest, seed: int) -> GraphState:
    return {
        "session_id": request.session_id,
        "prompt": request.prompt,
        "snapshot_refs": [item.ref for item in request.selected_snapshots],
        "target_count": request.target_count,
        "seed": seed,
        "max_proposal_attempts": request.max_proposal_attempts,
        "scenarios": [],
        "map_failures": [],
        "clarification_questions": [],
        "sampled_variants": [],
        "variant_index": 0,
        "proposal_attempts": 0,
        "feedback": [],
    }


def _llm(runtime: Runtime[GraphContext]) -> GeneratorLLM:
    return cast(GeneratorLLM, runtime.context["llm"])


def _variant(state: GraphState) -> SampledVariant:
    return SampledVariant.model_validate(state["sampled_variants"][state["variant_index"]])


def _snapshot(runtime: Runtime[GraphContext], variant: SampledVariant):
    return next(item for item in runtime.context["selected_snapshots"] if item.ref == variant.site.snapshot)


def extract_constraints(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    request = runtime.context["request"]
    try:
        constraints = _llm(runtime).extract_constraints(state["prompt"]).model_copy(deep=True)
    except LLMCallError as exc:
        return {"status": "failed", "map_failures": [
            MapFailure(snapshot=item.ref, code=exc.code, message=str(exc))
            for item in runtime.context["selected_snapshots"]
        ]}
    if constraints.ambiguities:
        return {"status": "needs_clarification", "clarification_questions": constraints.ambiguities}
    if constraints.unsupported_requirements:
        return {"status": "failed", "map_failures": [
            MapFailure(snapshot=item.ref, code="UNSUPPORTED_REQUIREMENT",
                       message="; ".join(constraints.unsupported_requirements))
            for item in runtime.context["selected_snapshots"]
        ]}
    if request.weather_conditions:
        constraints.weather_conditions = sorted(set(constraints.weather_conditions + request.weather_conditions))
    if request.lighting is not None:
        if constraints.lighting is not None and constraints.lighting != request.lighting:
            return {"status": "failed", "map_failures": [
                MapFailure(snapshot=item.ref, code="ENVIRONMENT_CONFLICT",
                           message="Ánh sáng trong prompt mâu thuẫn lựa chọn môi trường.")
                for item in runtime.context["selected_snapshots"]
            ]}
        constraints.lighting = request.lighting
    return {"constraints": constraints}


def after_extract(state: GraphState) -> str:
    return "finish" if state.get("status") else "find_sites"


def find_sites(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    found = find_cut_in_sites(runtime.context["selected_snapshots"], state["constraints"].location_tags)
    return {"sites": found.sites, "map_failures": found.map_failures}


def sample_contexts(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    request = runtime.context["request"]
    try:
        result = sample_variants(runtime.context["selected_snapshots"], state["sites"], state["constraints"],
                                 target_count=state["target_count"], seed=state["seed"],
                                 ego_blueprint_id=request.ego_blueprint_id, weather_preset=request.weather_preset)
    except SamplingError as exc:
        failed_maps = {failure.snapshot.map_name for failure in state["map_failures"]}
        failures = [MapFailure(snapshot=item.ref, code=exc.code, message=str(exc))
                    for item in runtime.context["selected_snapshots"] if item.ref.map_name not in failed_maps]
        return {"status": "failed", "map_failures": state["map_failures"] + failures}
    return {"sampled_variants": [item.model_dump(mode="python") for item in result.variants],
            "map_failures": state["map_failures"] + result.map_failures}


def after_sample(state: GraphState) -> str:
    return "select_variant" if state.get("sampled_variants") else "finish"


def select_variant(state: GraphState) -> dict:
    return {"current_plan": None, "validation": None, "proposal_attempts": 0, "feedback": []}


def propose_maneuver(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    try:
        plan = _llm(runtime).propose_maneuver(state["prompt"], _variant(state), feedback=state["feedback"])
    except LLMCallError as exc:
        return {"current_plan": None, "proposal_attempts": state["proposal_attempts"] + 1,
                "feedback": [f"{exc.code}: {exc}"]}
    return {"current_plan": plan, "proposal_attempts": state["proposal_attempts"] + 1}


def after_propose(state: GraphState) -> str:
    return "validate_plan" if state.get("current_plan") is not None else "reject_variant"


def validate_plan(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    variant = _variant(state)
    validation = validate_cut_in(state["current_plan"], variant, _snapshot(runtime, variant))
    return {"validation": validation,
            "feedback": [f"{issue.code}: {issue.message}" for issue in validation.issues]}


def after_validate(state: GraphState) -> str:
    validation: ValidationResult = state["validation"]
    if validation.valid:
        return "export_xosc"
    if (state["proposal_attempts"] < state["max_proposal_attempts"]
            and validation.issues and all(issue.recoverable for issue in validation.issues)):
        return "propose_maneuver"
    return "reject_variant"


def export_xosc(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    variant = _variant(state)
    try:
        xml, digest = render_xosc(state["current_plan"], variant, _snapshot(runtime, variant))
    except XoscExportError as exc:
        return {"map_failures": state["map_failures"] + [
            MapFailure(snapshot=variant.site.snapshot, code=exc.code, message=str(exc))
        ]}
    scenario_id = hashlib.sha256(
        f"{state['session_id']}:{len(state['scenarios']) + 1}:{digest}".encode()
    ).hexdigest()[:24]
    scenario = GeneratedScenario(
        scenario_id=scenario_id, variant_no=len(state["scenarios"]) + 1,
        snapshot=variant.site.snapshot, site_id=variant.site.site_id,
        plan=state["current_plan"], validation=state["validation"],
        xosc=xml, xosc_sha256=digest,
    )
    return {"scenarios": state["scenarios"] + [scenario]}


def reject_variant(state: GraphState) -> dict:
    variant = _variant(state)
    return {"map_failures": state["map_failures"] + [
        MapFailure(snapshot=variant.site.snapshot, code="NO_VALID_PLAN",
                   message="; ".join(state["feedback"]) or "Không tạo được plan hợp lệ.")
    ]}


def advance_variant(state: GraphState) -> dict:
    return {"variant_index": state["variant_index"] + 1}


def after_advance(state: GraphState) -> str:
    return "select_variant" if state["variant_index"] < len(state["sampled_variants"]) else "finish"


def finish(state: GraphState) -> dict:
    if state.get("status") == "needs_clarification":
        return {}
    count = len(state["scenarios"])
    status = "completed" if count == state["target_count"] else "partial" if count else "failed"
    return {"status": status}


def build_graph():
    builder = StateGraph(GraphState, context_schema=GraphContext)
    for name, node in (
        ("extract_constraints", extract_constraints),
        ("find_sites", find_sites),
        ("sample_contexts", sample_contexts),
        ("select_variant", select_variant),
        ("propose_maneuver", propose_maneuver),
        ("validate_plan", validate_plan),
        ("export_xosc", export_xosc),
        ("reject_variant", reject_variant),
        ("advance_variant", advance_variant),
        ("finish", finish),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "extract_constraints")
    builder.add_conditional_edges("extract_constraints", after_extract, ["find_sites", "finish"])
    builder.add_edge("find_sites", "sample_contexts")
    builder.add_conditional_edges("sample_contexts", after_sample, ["select_variant", "finish"])
    builder.add_edge("select_variant", "propose_maneuver")
    builder.add_conditional_edges("propose_maneuver", after_propose, ["validate_plan", "reject_variant"])
    builder.add_conditional_edges("validate_plan", after_validate,
                                  ["propose_maneuver", "export_xosc", "reject_variant"])
    builder.add_edge("export_xosc", "advance_variant")
    builder.add_edge("reject_variant", "advance_variant")
    builder.add_conditional_edges("advance_variant", after_advance, ["select_variant", "finish"])
    builder.add_edge("finish", END)
    return builder.compile(name="motorcycle_cut_in_v1")


GENERATION_GRAPH = build_graph()


def run_graph(request: GenerationRequest, llm: GeneratorLLM, seed: int) -> GraphState:
    # The bound is proportional to variants and possible repair turns; the
    # LangGraph default of 25 steps is too small for multi-map sessions.
    step_limit = max(50, request.target_count * (2 * request.max_proposal_attempts + 6) + 10)
    return cast(GraphState, GENERATION_GRAPH.invoke(
        initial_state(request, seed),
        context={"request": request, "selected_snapshots": request.selected_snapshots, "llm": llm},
        config={"recursion_limit": step_limit},
    ))
