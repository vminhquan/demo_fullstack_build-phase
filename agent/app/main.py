from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from langchain_core.callbacks import get_usage_metadata_callback

from app.config import get_settings
from app.contracts.api import CatalogRef, GenerateRequest, GenerateResponse, RefineRequest, RefineResponse, TitleRequest, TitleResponse, Usage
from app.contracts.catalog_v1 import CatalogV1
from app.scenario.graph import ScenarioGraph
from app.scenario.grounding import CatalogGrounder, GroundingError
from app.scenario.rag import ScenarioRAG
from app.scenario.refiner import refine
from app.scenario.schemas import ScenarioIR
from app.scenario.spec import SpecUnsupported, normalize_spec
from app.scenario.threat import evaluate_scenario_ir_threat
from app.scenario.title import summarize_title

log = logging.getLogger("scenario_forge.agent")


class AgentError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message, self.details = status_code, code, message, details or {}


class GrounderCache:
    """Lane indexes are rebuilt only when a different catalog snapshot (content hash) arrives."""

    def __init__(self, size: int = 4) -> None:
        self.size, self.items, self.lock = size, OrderedDict(), Lock()

    def get(self, catalog: CatalogV1) -> CatalogGrounder:
        key = catalog.content_hash
        if key is None:
            return CatalogGrounder(catalog)
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        grounder = CatalogGrounder(catalog)
        with self.lock:
            self.items[key] = grounder
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return grounder


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # In-memory Qdrant seeded from agent/knowledge; read-only at request time.
    app.state.rag = ScenarioRAG(location=":memory:") if settings.rag_enabled else None
    app.state.grounders = GrounderCache()
    yield


app = FastAPI(title="Scenario Forge Agent", version="1.0.0", lifespan=lifespan)


@app.exception_handler(AgentError)
async def agent_error_handler(_: Request, exc: AgentError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})


def usage_of(callback, started: float) -> Usage:
    totals = Usage(duration_ms=int((time.perf_counter() - started) * 1000))
    for item in (callback.usage_metadata or {}).values():
        totals.input_tokens += int(item.get("input_tokens", 0))
        totals.output_tokens += int(item.get("output_tokens", 0))
        totals.total_tokens += int(item.get("total_tokens", 0))
    return totals


def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().agent_api_key
    if expected and not (x_api_key and hmac.compare_digest(x_api_key.encode(), expected.encode())):
        raise AgentError(401, "UNAUTHORIZED", "Invalid agent API key")


@app.get("/health")
def health(request: Request) -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "rag_ready": getattr(request.app.state, "rag", None) is not None,
        "llm_enabled": settings.llm_enabled,
        "model": settings.model_name if settings.llm_enabled else None,
    }


@app.post("/v1/catalog/profile", dependencies=[Depends(verify_api_key)])
def catalog_profile(catalog: CatalogV1, request: Request) -> dict:
    """What this map can host (road types, multi-lane, oncoming lanes...): used for ODD feasibility."""
    try:
        return request.app.state.grounders.get(catalog).profile().to_dict()
    except GroundingError as exc:
        raise AgentError(422, exc.code, str(exc)) from exc


@app.post("/v1/prompts/refine", response_model=RefineResponse, dependencies=[Depends(verify_api_key)])
def refine_prompt(body: RefineRequest, request: Request) -> RefineResponse:
    """Structured form -> normalized spec + constraints + a concrete prompt for /v1/scenarios/generate."""
    try:
        grounder = request.app.state.grounders.get(body.catalog)
        normalized = normalize_spec(body.spec, grounder, seed=body.seed)
    except SpecUnsupported as exc:
        raise AgentError(422, exc.code, str(exc)) from exc
    except GroundingError as exc:
        raise AgentError(422, exc.code, str(exc)) from exc
    started = time.perf_counter()
    with get_usage_metadata_callback() as callback:
        result = refine(normalized, grounder, offline_mode=body.offline_mode)
    usage = usage_of(callback, started)
    return RefineResponse(
        usage=usage,
        refined_prompt=result.prompt,
        values=result.values,
        rationale=result.rationale,
        refine_mode=result.mode,
        model=result.model,
        spec=normalized.spec,
        constraints=normalized.constraints,
        adjustments=normalized.adjustments,
        road_type=normalized.road_type,
        weather=normalized.chosen_weather,
        warnings=result.warnings,
    )


@app.post("/v1/titles", response_model=TitleResponse, dependencies=[Depends(verify_api_key)])
def session_title(body: TitleRequest) -> TitleResponse:
    """Short title for a Test Case Builder session the user left untitled."""
    started = time.perf_counter()
    with get_usage_metadata_callback() as callback:
        title, mode = summarize_title(body.description)
    return TitleResponse(title=title, mode=mode, usage=usage_of(callback, started))


@app.post("/v1/scenarios/generate", response_model=GenerateResponse, dependencies=[Depends(verify_api_key)])
def generate(body: GenerateRequest, request: Request) -> GenerateResponse:
    # Sync handler: FastAPI runs it in the thread pool, so blocking LLM calls do not stall the loop.
    settings = get_settings()
    started = time.perf_counter()
    try:
        with get_usage_metadata_callback() as callback:
            grounder = request.app.state.grounders.get(body.catalog)
            graph = ScenarioGraph(grounder=grounder, rag=request.app.state.rag, offline_mode=body.offline_mode)
            state = graph.invoke(body.prompt, auto_repair=body.auto_repair, seed=body.seed, constraints=body.constraints)
    except GroundingError as exc:
        raise AgentError(422, exc.code, str(exc), {"usage": usage_of(callback, started).model_dump()}) from exc
    except Exception as exc:  # LLM/network failures surface as a gateway error, never as a stack trace.
        log.exception("Scenario generation failed")
        raise AgentError(502, "GENERATION_FAILED", f"Scenario generation failed: {type(exc).__name__}") from exc
    usage = usage_of(callback, started)

    status = state.get("status")
    if status == "REGULATION_ANSWERED":
        raise AgentError(422, "NOT_A_SCENARIO_REQUEST", "The prompt is a regulation question, not a scenario request",
                         {"regulation_answer": state.get("regulation_answer"), "usage": usage.model_dump()})
    if status != "COMPLETED":
        raise AgentError(422, state.get("error_code") or "GENERATION_STOPPED", state.get("error_message") or f"Generation stopped at {status}",
                         {"validation": state.get("validation_result"), "scenario_ir": state.get("scenario_ir"), "usage": usage.model_dump()})

    ir = ScenarioIR(**state["scenario_ir"])
    xml = state["xosc"]
    return GenerateResponse(
        generation_mode="llm" if graph.uses_llm else "deterministic",
        model=settings.model_name if graph.uses_llm else None,
        catalog=CatalogRef(map_name=grounder.map_name, carla_version=body.catalog.carla_version, content_hash=body.catalog.content_hash),
        scenario_ir=state["scenario_ir"],
        interpretation=state.get("interpretation") or {},
        validation=state.get("validation_result") or {},
        threat_score=evaluate_scenario_ir_threat(ir).to_dict(),
        grounding=state["grounding"],
        xosc=xml,
        xosc_sha256=hashlib.sha256(xml.encode("utf-8")).hexdigest(),
        xosc_validation=state.get("xosc_validation") or {},
        retrieved_regulations=[item["doc_id"] for item in state.get("retrieved_regulations", [])],
        retrieved_history=[item["doc_id"] for item in state.get("retrieved_history", [])],
        warnings=state.get("warnings", []),
        usage=usage,
    )
