"""HTTP-facing entry point for the LangGraph generation workflow."""

from __future__ import annotations

import secrets

from app.contracts import GenerationRequest, GenerationResponse
from app.graph import GeneratorLLM, run_graph


def generate(request: GenerationRequest, llm: GeneratorLLM) -> GenerationResponse:
    seed = request.seed if request.seed is not None else secrets.randbits(64)
    state = run_graph(request, llm, seed)
    return GenerationResponse(
        session_id=request.session_id,
        status=state["status"],
        target_count=request.target_count,
        seed=seed,
        model_name=llm.model_name,
        scenarios=state["scenarios"],
        map_failures=state["map_failures"],
        clarification_questions=state["clarification_questions"],
    )
