"""HTTP-facing entry point for the LangGraph generation workflow."""

from __future__ import annotations

import secrets

from app.bridges import CarlaCheck
from app.contracts import GenerationRequest, GenerationResponse
from app.graph import ChatLLM, run_graph
from app.llm.tools import CatalogApi


def generate(request: GenerationRequest, llm: ChatLLM, catalog_api: CatalogApi,
             carla: CarlaCheck | None = None) -> GenerationResponse:
    seed = request.seed if request.seed is not None else secrets.randbits(64)
    state = run_graph(request, llm, seed, catalog_api, carla)
    scenario = state.get("scenario")
    return GenerationResponse(
        session_id=request.session_id,
        status=state["status"],
        target_count=request.target_count,
        seed=seed,
        model_name=llm.model_name,
        usage=state["usage"],
        scenarios=[scenario] if scenario is not None else [],
        map_failures=state["map_failures"],
    )
