"""LangGraph workflow for catalog-grounded motorcycle cut-in generation.

Only prompt interpretation and maneuver proposal call the LLM; both may call
tools (app.llm.tools) that read the stored snapshot through the Backend or run
the validator. Every map, vehicle, environment and XOSC decision is delegated to
deterministic Python.
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
from app.cut_in.sample import SampledVariant, SamplingError, available_environments, sample_variants
from app.cut_in.validate import repair_maneuver, validate_cut_in
from app.cut_in.xosc import XoscExportError, render_xosc
from app.llm.client import LLMCallError
from app.llm.tools import CatalogApi, ExtractionTools, ProposalTools, Toolbox
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime


class GeneratorLLM(Protocol):
    model_name: str

    def extract_constraints(
        self, prompt: str, *, tools: Toolbox | None = None, map_names: list[str] | None = None,
        environments: list[str] | None = None,
    ) -> PromptConstraints: ...

    def propose_maneuver(
        self, prompt: str, variant: SampledVariant, *, feedback: list[str] | None = None, tools: Toolbox | None = None,
    ) -> CutInPlan: ...


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
    api = runtime.context.get("catalog_api")
    refs = [item.ref for item in runtime.context["selected_snapshots"]]
    tools = ExtractionTools(api, refs) if api is not None else None
    try:
        constraints = _llm(runtime).extract_constraints(
            state["prompt"], tools=tools, map_names=[ref.map_name for ref in refs],
            environments=available_environments(runtime.context["selected_snapshots"]),
        ).model_copy(deep=True)
    except LLMCallError as exc:
        return {"status": "failed", "map_failures": [
            MapFailure(snapshot=item.ref, code=exc.code, message=str(exc))
            for item in runtime.context["selected_snapshots"]
        ]}
    # Missing or unclear details never stop a run: whatever the prompt leaves open is sampled across the
    # supported range. The notes travel with the result so the user can see what was assumed.
    notes = {"clarification_questions": constraints.ambiguities} if constraints.ambiguities else {}
    if constraints.unsupported_requirements:
        return {"status": "failed", "map_failures": [
            MapFailure(snapshot=item.ref, code="UNSUPPORTED_REQUIREMENT",
                       message="; ".join(constraints.unsupported_requirements))
            for item in runtime.context["selected_snapshots"]
        ]}
    # The prompt is the more specific ask: when it names weather, light or road surface, the environment picked
    # in the form (often "any of these" ticks) is set aside instead of being merged into an impossible mix such
    # as "mưa tầm tã" + clear. A prompt silent on the environment takes the picked one.
    if constraints.weather_conditions or constraints.lighting or constraints.road_surface:
        notes["use_request_environment"] = False
    else:
        constraints.weather_conditions = sorted(set(request.weather_conditions))
        constraints.lighting = request.lighting
        notes["use_request_environment"] = True
    return {"constraints": constraints, **notes}


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
                                 ego_blueprint_id=request.ego_blueprint_id,
                                 weather_preset=request.weather_preset if state.get("use_request_environment", True) else None)
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
    variant = _variant(state)
    tools = ProposalTools(runtime.context.get("catalog_api"), variant, _snapshot(runtime, variant))
    try:
        plan = _llm(runtime).propose_maneuver(state["prompt"], variant, feedback=state["feedback"], tools=tools)
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
    recoverable = bool(validation.issues) and all(issue.recoverable for issue in validation.issues)
    if recoverable and state["proposal_attempts"] < state["max_proposal_attempts"]:
        return "propose_maneuver"
    return "repair_plan" if recoverable else "reject_variant"


def repair_plan(state: GraphState, runtime: Runtime[GraphContext]) -> dict:
    """The LLM kept missing the motion checks: compute the nearest numbers that pass them."""
    variant = _variant(state)
    repaired = repair_maneuver(state["current_plan"], variant)
    if repaired is None:
        return {"validation": None}
    validation = validate_cut_in(repaired, variant, _snapshot(runtime, variant))
    return {"current_plan": repaired, "validation": validation,
            "feedback": [f"{issue.code}: {issue.message}" for issue in validation.issues]}


def after_repair(state: GraphState) -> str:
    return "export_xosc" if state["validation"] is not None and state["validation"].valid else "reject_variant"


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
        ("repair_plan", repair_plan),
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
                                  ["propose_maneuver", "export_xosc", "repair_plan", "reject_variant"])
    builder.add_conditional_edges("repair_plan", after_repair, ["export_xosc", "reject_variant"])
    builder.add_edge("export_xosc", "advance_variant")
    builder.add_edge("reject_variant", "advance_variant")
    builder.add_conditional_edges("advance_variant", after_advance, ["select_variant", "finish"])
    builder.add_edge("finish", END)
    return builder.compile(name="motorcycle_cut_in_v1")


GENERATION_GRAPH = build_graph()


def run_graph(request: GenerationRequest, llm: GeneratorLLM, seed: int, catalog_api: CatalogApi | None = None) -> GraphState:
    # The bound is proportional to variants and possible repair turns; the
    # LangGraph default of 25 steps is too small for multi-map sessions.
    step_limit = max(50, request.target_count * (2 * request.max_proposal_attempts + 7) + 10)
    return cast(GraphState, GENERATION_GRAPH.invoke(
        initial_state(request, seed),
        context={"request": request, "selected_snapshots": request.selected_snapshots, "llm": llm,
                 "catalog_api": catalog_api},
        config={"recursion_limit": step_limit},
    ))
