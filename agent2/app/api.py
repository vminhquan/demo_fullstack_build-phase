from functools import lru_cache

from app import logs
from app.bridges import BridgeRegistry, CarlaCheck, verify_bridge_token
from app.config import Settings, get_settings
from app.contracts import GenerationRequest, GenerationResponse
from app.llm.client import SERVICE_CODES, LLMCallError, from_settings
from app.llm.tools import CatalogApi
from app.service import generate
from fastapi import FastAPI, Header, HTTPException, WebSocket

app = FastAPI(title="Scenario Forge Agent 2")
logs.setup(get_settings().log_level, get_settings().log_max_chars)
# Bridges connected for CARLA validation (app.bridges); one uvicorn worker holds them all.
BRIDGES = BridgeRegistry()


@lru_cache
def _catalog_api(backend_url: str, api_key: str | None) -> CatalogApi:
    # One pooled HTTP client for the process, not one per request.
    return CatalogApi(backend_url, api_key)


def _carla_check(settings: Settings) -> CarlaCheck | None:
    if settings.carla_validation == "off":
        return None
    return CarlaCheck(BRIDGES, settings.carla_validation, settings.carla_validation_timeout_s)


@app.websocket("/v1/bridge/ws")
async def bridge_socket(websocket: WebSocket) -> None:
    """A Bridge's own channel for validate.request. Auth: `Authorization: Bearer <token from bridge.welcome>`."""
    settings = get_settings()
    secret = settings.bridge_agent_secret.get_secret_value() if settings.bridge_agent_secret else ""
    header = websocket.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    bridge_uid = verify_bridge_token(secret, token)
    if bridge_uid is None:
        await websocket.close(code=1008, reason="invalid bridge token")
        return
    await websocket.accept()
    await BRIDGES.serve(bridge_uid, websocket)


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
    if not settings.backend_url:
        # The LLM reads the catalog and the save node stores the XOSC only through the Backend.
        raise HTTPException(status_code=503, detail={"code": "BACKEND_NOT_CONFIGURED", "message": "Thiếu BACKEND_URL cho agent2."})
    key = settings.agent_api_key.get_secret_value() if settings.agent_api_key else None
    result = generate(body, llm, _catalog_api(settings.backend_url, key), _carla_check(settings))
    if result.status in {"failed", "needs_clarification"}:
        first = result.map_failures[0] if result.map_failures else None
        # The LLM service failed (no credit, rate limit...): 503, so the Backend reports the Agent, not the prompt.
        raise HTTPException(status_code=503 if first and first.code in SERVICE_CODES else 422, detail={
            "code": first.code if first else "NEEDS_CLARIFICATION",
            "message": first.message if first else "Cần làm rõ prompt.",
            "details": {"map_failures": [item.model_dump() for item in result.map_failures],
                        "clarification_questions": result.clarification_questions,
                        "usage": result.usage},
        })
    return result
