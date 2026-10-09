from app.config import get_settings
from app.contracts import GenerationRequest, GenerationResponse
from app.llm.client import LLMCallError, from_settings
from app.service import generate
from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Scenario Forge Agent 2")


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "model": settings.model_name}


@app.post("/v1/scenarios/generate", response_model=GenerationResponse)
def generate_scenarios(body: GenerationRequest, x_api_key: str | None = Header(default=None)) -> GenerationResponse:
    settings = get_settings()
    expected = settings.agent_api_key.get_secret_value() if settings.agent_api_key else None
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid Agent API key")
    try:
        llm = from_settings(settings)
    except LLMCallError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
    result = generate(body, llm)
    if result.status in {"failed", "needs_clarification"}:
        first = result.map_failures[0] if result.map_failures else None
        raise HTTPException(status_code=422, detail={
            "code": first.code if first else "NEEDS_CLARIFICATION",
            "message": first.message if first else "Cần làm rõ prompt.",
            "details": {"map_failures": [item.model_dump() for item in result.map_failures],
                        "clarification_questions": result.clarification_questions},
        })
    return result
