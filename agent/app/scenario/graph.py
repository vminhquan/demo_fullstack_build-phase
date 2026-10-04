"""
ScenarioForge — LangGraph scenario-generation workflow, grounded on a catalog.v1 map.

    detect_intent ──REGULATION_QUERY──▶ query_regulation ─▶ END
          │
          ▼
    generate_spatial ─▶ generate_ego ─▶ generate_actors ─▶ generate_triggers ─▶ generate_weather
          ─▶ assemble_ir ─▶ validate_guardrails ──invalid & auto_repair──▶ smt_repair ─┐
                                   │  ▲─────────────────────────────────────────────────┘
                                   ▼ valid
                           ground_and_export (catalog lanes + blueprints ─▶ .xosc + XSD check) ─▶ END

Ported from P-065 `src/agents/graph.py`. Differences: one orchestrator per request bound to the
request's catalog; no pre-generation HITL loop (the product Backend reviews the result); nothing
is indexed back into RAG; the CARLA Python script compiler is replaced by the XOSC exporter.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.scenario.generator import DecomposedScenarioGenerator
from app.scenario.grounding import CatalogGrounder, GroundingError
from app.scenario.rag import ScenarioRAG
from app.scenario.schemas import (
    ActorsStepOutput,
    EgoStepOutput,
    ExpectedVerdict,
    ScenarioInterpretation,
    ScenarioIR,
    SpatialStepOutput,
    TriggersStepOutput,
    WeatherEnvStepOutput,
)
from app.scenario.smt import enhance_or_repair_scenario_ir
from app.scenario.spec import GenerationConstraints, apply_constraints
from app.scenario.state import ScenarioState
from app.scenario.validator import ScenarioGuardrailValidator
from app.scenario.xosc import XoscExporter, validate_xosc

class ScenarioGraph:
    def __init__(self, *, grounder: CatalogGrounder, rag: ScenarioRAG | None, offline_mode: bool = False) -> None:
        self.grounder = grounder
        self.rag = rag
        self.profile = grounder.profile()
        self.generator = DecomposedScenarioGenerator(
            rag=rag,
            offline_mode=offline_mode,
            map_name=grounder.map_name,
            map_context=self.profile.prompt_text(),
        )
        self.exporter = XoscExporter(flip_y=get_settings().xosc_flip_y)
        self.graph = self._build()

    @property
    def uses_llm(self) -> bool:
        return self.generator._has_llm_credentials()

    # ------------------------------------------------------------------ nodes
    def detect_intent(self, state: ScenarioState) -> ScenarioState:
        prompt = state["prompt"]
        interp = self.generator.step1_interpret(prompt)
        found: dict = {"retrieved_regulations": [], "retrieved_project_documents": [], "retrieved_components": [], "retrieved_history": []}
        if self.rag is not None:
            found = {
                "retrieved_regulations": [r.to_dict() for r in self.rag.retrieve_regulations(prompt, limit=3)],
                "retrieved_project_documents": [d.to_dict() for d in self.rag.retrieve_project_documents(prompt, limit=3)],
                "retrieved_components": [c.to_dict() for c in self.rag.retrieve_components(prompt, limit=3)],
                "retrieved_history": [h.to_dict() for h in self.rag.retrieve_execution_history(prompt, limit=2)],
            }
        return {**found, "intent_type": interp.intent_type, "interpretation": interp.model_dump(), "status": "INTENT_DETECTED"}

    def query_regulation(self, state: ScenarioState) -> ScenarioState:
        regs = state.get("retrieved_regulations", [])
        docs = state.get("retrieved_project_documents", [])
        if not regs and not docs:
            return {"regulation_answer": "No matching reference found in the knowledge base.", "status": "REGULATION_ANSWERED"}
        lines = ["Relevant retrieved references:"]
        for r in regs:
            pl = r.get("payload", {})
            lines.append(f"- [{r['doc_id']}] {pl.get('standard', '')} ({pl.get('title', '')}): {pl.get('description', '')} | SOTIF: {pl.get('sotif_notes', '')}")
        for d in docs:
            pl = d.get("payload", {})
            lines.append(f"- [{pl.get('source', '')} § {pl.get('section', '')}] {d.get('text', '')[:900]}")
        return {"regulation_answer": "\n".join(lines), "status": "REGULATION_ANSWERED"}

    def generate_spatial(self, state: ScenarioState) -> ScenarioState:
        interp = ScenarioInterpretation(**state["interpretation"])
        spatial = self.generator.step2_generate_spatial(interp)
        warnings = list(state.get("warnings", []))
        hostable = self.profile.road_types
        constraints = self._constraints(state)
        # The structured form fixes the road layout (already checked against this map).
        if constraints and constraints.road_type in hostable:
            spatial.road_type = constraints.road_type
        # The user picked this map; a scenario it cannot host is not generated (no swap to another road type).
        if spatial.road_type not in hostable:
            raise GroundingError(
                "MAP_CANNOT_HOST_ROAD_TYPE",
                f"{self.grounder.map_name} has no '{spatial.road_type}' road; the scenario is not generated on this map",
            )
        # The map is fixed by the selected catalog; the model never chooses it.
        spatial.map_name = self.grounder.map_name
        return {"spatial_step": spatial.model_dump(), "warnings": warnings, "status": "SPATIAL_GENERATED"}

    def generate_ego(self, state: ScenarioState) -> ScenarioState:
        interp = ScenarioInterpretation(**state["interpretation"])
        spatial = SpatialStepOutput(**state["spatial_step"])
        ego_step = self.generator.step3_generate_ego(interp, spatial)
        ego_step.ego.road_type = spatial.road_type
        return {"ego_step": ego_step.model_dump(), "status": "EGO_GENERATED"}

    def generate_actors(self, state: ScenarioState) -> ScenarioState:
        interp = ScenarioInterpretation(**state["interpretation"])
        actors = self.generator.step4_generate_actors(interp, SpatialStepOutput(**state["spatial_step"]), EgoStepOutput(**state["ego_step"]))
        return {"actors_step": actors.model_dump(mode="json"), "status": "ACTORS_GENERATED"}

    def generate_triggers(self, state: ScenarioState) -> ScenarioState:
        interp = ScenarioInterpretation(**state["interpretation"])
        triggers = self.generator.step5_generate_triggers(interp, EgoStepOutput(**state["ego_step"]), ActorsStepOutput(**state["actors_step"]))
        return {"triggers_step": triggers.model_dump(mode="json"), "status": "TRIGGERS_GENERATED"}

    def generate_weather(self, state: ScenarioState) -> ScenarioState:
        weather = self.generator.step6_generate_weather(ScenarioInterpretation(**state["interpretation"]))
        return {"weather_step": weather.model_dump(mode="json"), "status": "WEATHER_GENERATED"}

    def assemble_ir(self, state: ScenarioState) -> ScenarioState:
        interp = ScenarioInterpretation(**state["interpretation"])
        spatial = SpatialStepOutput(**state["spatial_step"])
        triggers = TriggersStepOutput(**state["triggers_step"])
        weather = WeatherEnvStepOutput(**state["weather_step"])
        ir = ScenarioIR(
            name=interp.scenario_name,
            description=state["prompt"],
            ego=EgoStepOutput(**state["ego_step"]).ego,
            actors=triggers.actors,
            weather=weather.weather,
            time_of_day_hour=weather.time_of_day_hour,
            map_name=spatial.map_name,
            occlusions=spatial.occlusions,
            expected_outcome=triggers.expected_outcome,
        )
        ir, notes = apply_constraints(ir, self._constraints(state))
        return {"scenario_ir": ir.model_dump(mode="json"), "warnings": [*state.get("warnings", []), *notes], "status": "IR_ASSEMBLED"}

    def validate_guardrails(self, state: ScenarioState) -> ScenarioState:
        result = ScenarioGuardrailValidator.validate_scenario(ScenarioIR(**state["scenario_ir"]))
        return {
            "validation_result": result.to_dict(),
            "status": "VALIDATED" if result.is_valid else "GUARDRAIL_FAILED",
            "error_code": None if result.is_valid else "GUARDRAIL_FAILED",
            "error_message": None if result.is_valid else "; ".join(result.errors),
        }

    def smt_repair(self, state: ScenarioState) -> ScenarioState:
        repaired = enhance_or_repair_scenario_ir(ScenarioIR(**state["scenario_ir"]))
        # A repair must not leave the form's ranges; if that makes it invalid again, validation reports it.
        repaired, notes = apply_constraints(repaired, self._constraints(state))
        return {"scenario_ir": repaired.model_dump(mode="json"), "warnings": [*state.get("warnings", []), *notes],
                "repair_attempts": state.get("repair_attempts", 0) + 1, "status": "SMT_REPAIRED"}

    def ground_and_export(self, state: ScenarioState) -> ScenarioState:
        ir = ScenarioIR(**state["scenario_ir"])
        try:
            constraints = self._constraints(state)
            grounded = self.grounder.ground(ir, seed=state.get("seed"), ego_blueprint=constraints.ego_blueprint if constraints else None)
        except GroundingError as exc:
            return {"status": "GROUNDING_FAILED", "error_code": exc.code, "error_message": str(exc)}
        xml = self.exporter.to_xml_string(grounded)
        return {
            "scenario_ir": grounded.ir.model_dump(mode="json"),
            "grounding": grounded.to_dict(),
            "xosc": xml,
            "xosc_validation": validate_xosc(xml),
            "warnings": [*state.get("warnings", []), *grounded.warnings],
            "status": "COMPLETED",
            "error_code": None,
            "error_message": None,
        }

    @staticmethod
    def _constraints(state: ScenarioState) -> GenerationConstraints | None:
        raw = state.get("constraints")
        return GenerationConstraints(**raw) if raw else None

    # ------------------------------------------------------------------ routing
    @staticmethod
    def _after_intent(state: ScenarioState) -> str:
        return "query_regulation" if state.get("intent_type") == "REGULATION_QUERY" else "generate_spatial"

    @staticmethod
    def _after_validation(state: ScenarioState) -> str:
        result = state.get("validation_result") or {}
        if result.get("is_valid") and result.get("verdict") != ExpectedVerdict.REJECT_INVALID.value:
            return "ground_and_export"
        if state.get("auto_repair") and state.get("repair_attempts", 0) < state.get("max_repair_attempts", 2):
            return "smt_repair"
        return "end"

    def _build(self):
        workflow = StateGraph(ScenarioState)
        steps = ("generate_spatial", "generate_ego", "generate_actors", "generate_triggers", "generate_weather", "assemble_ir", "validate_guardrails")
        for name in ("detect_intent", "query_regulation", *steps, "smt_repair", "ground_and_export"):
            workflow.add_node(name, getattr(self, name))
        workflow.set_entry_point("detect_intent")
        workflow.add_conditional_edges("detect_intent", self._after_intent, {"query_regulation": "query_regulation", "generate_spatial": "generate_spatial"})
        workflow.add_edge("query_regulation", END)
        for current, following in zip(steps, steps[1:]):
            workflow.add_edge(current, following)
        workflow.add_conditional_edges("validate_guardrails", self._after_validation, {"ground_and_export": "ground_and_export", "smt_repair": "smt_repair", "end": END})
        workflow.add_edge("smt_repair", "validate_guardrails")
        workflow.add_edge("ground_and_export", END)
        return workflow.compile()

    def invoke(self, prompt: str, *, auto_repair: bool = True, seed: int | None = None,
               constraints: GenerationConstraints | None = None) -> ScenarioState:
        return self.graph.invoke({
            "prompt": prompt,
            "constraints": constraints.model_dump(mode="json") if constraints else None,
            "auto_repair": auto_repair,
            "seed": seed,
            "repair_attempts": 0,
            "max_repair_attempts": 2,
            "warnings": [],
        })
