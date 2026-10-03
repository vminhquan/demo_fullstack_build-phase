from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.modules.audit.router import router as audit_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.execution.router import router as execution_router
from app.modules.identity.router import router as auth_router
from app.modules.project.router import router as project_router
from app.modules.review.router import router as review_router
from app.modules.search.router import router as rag_search_router
from app.modules.storage.router import router as storage_router
from app.modules.testcase.router import router as testcase_router
from app.modules.testcase.router import versions_router
from app.modules.testsuite.router import router as testsuite_router
from app.modules.testsuite.router import suite_runs_router
from app.shared.config import get_settings
from app.shared.domain.errors import DomainError
from app.shared.infrastructure.audit import current_request_id

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Scenario Forge Flow B API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_id(request: Request, call_next):
    # audit_logs.request_id is String(64); never trust client-supplied length.
    request.state.request_id = (request.headers.get("X-Request-ID") or "").strip()[:64] or secrets.token_hex(16)
    token = current_request_id.set(request.state.request_id)
    try:
        response = await call_next(request)
    finally:
        current_request_id.reset(token)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details, "request_id": getattr(request.state, "request_id", None)}})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    # Concurrent inserts racing on unique constraints (email, tag name, ...) are conflicts, not server errors.
    return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "The request conflicts with existing data", "details": {}, "request_id": getattr(request.state, "request_id", None)}})


@app.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def health_ready() -> dict[str, str]:
    return {"status": "ready"}


for router in (auth_router, project_router, testcase_router, versions_router, review_router, rag_search_router, testsuite_router, suite_runs_router, execution_router, storage_router, audit_router, dashboard_router):
    app.include_router(router, prefix=settings.api_prefix)
