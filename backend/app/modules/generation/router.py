from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.modules.catalog.schemas import CatalogSnapshotResponse
from app.modules.catalog.service import get_snapshot, resolve_for_generation
from app.modules.generation.agent_client import AgentPort, get_agent
from app.modules.generation.mapping import metadata_constraints, suggested_metadata
from app.modules.generation.schemas import (
    GenerationAccept,
    GenerationCreate,
    GenerationRefine,
    GenerationResponse,
    RefineResponse,
    SuggestedMetadata,
)
from app.modules.identity.dependencies import Principal, require
from app.modules.testcase.schemas import TestCaseContent
from app.modules.testcase.service import create_test_case
from app.shared.domain.errors import AgentUnavailable, CatalogUnavailable, Conflict, Forbidden, GenerationRejected, NotFound, ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import AgentCall, CarlaCatalogSnapshot, GenerationStatus, ScenarioGeneration, TestCase

router = APIRouter(prefix="/scenario-generations", tags=["scenario-generations"])


def generation_response(generation: ScenarioGeneration, snapshot: CarlaCatalogSnapshot) -> GenerationResponse:
    result = generation.result
    return GenerationResponse(
        id=generation.id,
        status=generation.status,
        prompt=generation.prompt,
        catalog=CatalogSnapshotResponse.from_model(snapshot),
        generation_mode=generation.generation_mode,
        model=generation.model,
        scenario_ir=result.get("scenario_ir", {}),
        form=result.get("form"),
        cache_hit=generation.cache_hit,
        usage=result.get("usage", {}),
        interpretation=result.get("interpretation", {}),
        validation=result.get("validation", {}),
        threat_score=result.get("threat_score", {}),
        grounding=result.get("grounding", {}),
        xosc_validation=result.get("xosc_validation", {}),
        retrieved_regulations=result.get("retrieved_regulations", []),
        warnings=result.get("warnings", []),
        xosc=generation.xosc,
        xosc_sha256=generation.xosc_sha256,
        suggested_metadata=SuggestedMetadata(**suggested_metadata(result)),
        accepted_test_case_id=generation.accepted_test_case_id,
        created_by=generation.created_by,
        created_at=generation.created_at,
    )


async def get_generation(session: AsyncSession, project_id: int, generation_id: int, *, locked: bool = False) -> ScenarioGeneration:
    statement = (
        select(ScenarioGeneration)
        .where(ScenarioGeneration.id == generation_id, ScenarioGeneration.project_id == project_id)
        .options(undefer(ScenarioGeneration.xosc))
    )
    if locked:
        statement = statement.with_for_update()
    generation = await session.scalar(statement)
    if generation is None:
        raise NotFound("Scenario generation not found")
    return generation


def request_hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


async def log_call(session: AsyncSession, actor: Principal, **fields: Any) -> AgentCall:
    """Record one Agent request; committed right away so failures are counted even when the request then errors."""
    usage = fields.pop("usage", None) or {}
    call = AgentCall(
        project_id=actor.project_id, user_id=actor.id,
        input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)),
        duration_ms=int(usage.get("duration_ms", 0)), **fields,
    )
    session.add(call)
    await session.commit()
    return call


async def call_agent(session: AsyncSession, actor: Principal, kind: str, request, **context: Any) -> dict[str, Any]:
    """Run an Agent coroutine and log the outcome (COMPLETED / REJECTED / FAILED) with its token usage."""
    try:
        result = await request
    except GenerationRejected as exc:
        await log_call(session, actor, kind=kind, status="REJECTED", error_code=exc.code, usage=exc.details.get("usage"), **context)
        raise
    except AgentUnavailable:
        await log_call(session, actor, kind=kind, status="FAILED", error_code="AGENT_UNAVAILABLE", **context)
        raise
    return result


async def snapshot_for_map(session: AsyncSession, project_id: int, snapshot_id: int | None, map_code: str) -> CarlaCatalogSnapshot:
    """CARLA data with lanes for `map_code`: the given snapshot, else the user's own data, else the shipped default."""
    if snapshot_id is not None:
        snapshot = await resolve_for_generation(session, project_id, snapshot_id=snapshot_id, source="PROJECT", map_name=None)
        if snapshot.map_name != map_code:
            raise ValidationFailed(f"The selected CARLA data is for {snapshot.map_name}, not {map_code}")
        return snapshot
    for source in ("PROJECT", "DEFAULT"):
        try:
            return await resolve_for_generation(session, project_id, snapshot_id=None, source=source, map_name=map_code)
        except CatalogUnavailable:
            continue
    raise CatalogUnavailable(
        f"No CARLA lane data for map {map_code}: the Agent can only generate on a map with lane data. "
        "Choose a map from the 'lane data' group or import that map's CARLA export"
    )


@router.post("/refine", response_model=RefineResponse)
async def refine_prompt(
    body: GenerationRefine,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort = Depends(get_agent),
) -> RefineResponse:
    """Structured form -> concrete prompt + constraints (nothing stored until the user generates)."""
    snapshot = await resolve_for_generation(session, actor.project_id, snapshot_id=body.catalog_snapshot_id, source=body.catalog_source, map_name=None)
    await session.commit()
    context = {"catalog_snapshot_id": snapshot.id, "from_form": True}
    result = await call_agent(session, actor, "REFINE", agent.refine(spec=body.spec, catalog=snapshot.catalog, seed=body.seed), **context)
    await log_call(session, actor, kind="REFINE", status="COMPLETED", usage=result.get("usage"),
                   generation_mode=result.get("refine_mode"), **context)
    return RefineResponse(catalog=CatalogSnapshotResponse.from_model(snapshot), **result)


@router.post("", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
async def create_generation(
    body: GenerationCreate,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort = Depends(get_agent),
) -> GenerationResponse:
    constraints = body.constraints
    if body.metadata is not None:
        snapshot = await snapshot_for_map(session, actor.project_id, body.catalog_snapshot_id, body.metadata.map_code)
        meta = body.metadata
        if meta.ego_vehicle_code and meta.ego_vehicle_code not in {item.get("id") for item in snapshot.catalog.get("vehicles", [])}:
            raise ValidationFailed(f"Ego vehicle '{meta.ego_vehicle_code}' is not in the CARLA data of {snapshot.map_name}")
        constraints = {
            **metadata_constraints(ego_vehicle_code=meta.ego_vehicle_code, adversary_type=meta.adversary_type, environment_code=meta.environment_code),
            **(constraints or {}),
        }
    else:
        snapshot = await resolve_for_generation(
            session, actor.project_id, snapshot_id=body.catalog_snapshot_id, source=body.catalog_source, map_name=body.map_name
        )
    key = request_hash(body.prompt, snapshot.content_hash, constraints, body.seed, body.auto_repair)
    context = {"catalog_snapshot_id": snapshot.id, "from_form": body.spec is not None or constraints is not None}
    cached = None
    if body.use_cache:
        cached = await session.scalar(
            select(ScenarioGeneration)
            .where(ScenarioGeneration.project_id == actor.project_id, ScenarioGeneration.request_hash == key)
            .options(undefer(ScenarioGeneration.xosc))
            .order_by(ScenarioGeneration.id.desc())
            .limit(1)
        )
    if cached is not None:
        # Identical request (same prompt, CARLA data, constraints, seed): reuse the result, no LLM tokens spent.
        result = {**cached.result, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "duration_ms": 0}}
        xosc = cached.xosc
    else:
        # Release the read transaction while the Agent works (LLM calls take seconds).
        await session.commit()
        result = await call_agent(session, actor, "GENERATE", agent.generate(
            prompt=body.prompt, catalog=snapshot.catalog, auto_repair=body.auto_repair, seed=body.seed, constraints=constraints,
            snapshot_id=snapshot.id, content_hash=snapshot.content_hash,
            environment_code=body.metadata.environment_code if body.metadata else None,
        ), **context)
        xosc = result.pop("xosc")
    if body.spec is not None or constraints is not None:
        result["form"] = {"spec": body.spec, "constraints": constraints, "metadata": body.metadata.model_dump() if body.metadata else None}
    generation = ScenarioGeneration(
        request_hash=key,
        cache_hit=cached is not None,
        project_id=actor.project_id,
        created_by=actor.id,
        prompt=body.prompt,
        catalog_snapshot_id=snapshot.id,
        status=GenerationStatus.COMPLETED,
        generation_mode=result.get("generation_mode", "unknown"),
        model=result.get("model"),
        result=result,
        xosc=xosc,
        xosc_sha256=result.get("xosc_sha256", ""),
    )
    session.add(generation)
    await session.flush()
    session.add(AgentCall(
        project_id=actor.project_id, user_id=actor.id, kind="GENERATE", status="COMPLETED", generation_id=generation.id,
        generation_mode=generation.generation_mode, cache_hit=cached is not None,
        input_tokens=int(result.get("usage", {}).get("input_tokens", 0)), output_tokens=int(result.get("usage", {}).get("output_tokens", 0)),
        duration_ms=int(result.get("usage", {}).get("duration_ms", 0)), **context,
    ))
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SCENARIO_GENERATED",
        entity_type="SCENARIO_GENERATION",
        entity_id=generation.id,
        after_data={
            "catalog_snapshot_id": snapshot.id,
            "map_name": snapshot.map_name,
            "generation_mode": generation.generation_mode,
            "xosc_sha256": generation.xosc_sha256,
        },
    )
    await session.commit()
    return generation_response(generation, snapshot)


@router.get("/{generation_id}", response_model=GenerationResponse)
async def read_generation(
    generation_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> GenerationResponse:
    generation = await get_generation(session, actor.project_id, generation_id)
    snapshot = await get_snapshot(session, actor.project_id, generation.catalog_snapshot_id)
    return generation_response(generation, snapshot)


async def save_generation_as_case(
    generation_id: int,
    body: GenerationAccept,
    actor: Principal,
    session: AsyncSession,
    *,
    builder_session_id: int | None = None,
    builder_variant_no: int | None = None,
) -> TestCase:
    """Save an Agent result as a PENDING test case (Test Case Builder calls this for every variant).

    Caller commits. Not exposed over HTTP: generating and saving is one step, there is no draft to accept.
    """
    generation = await get_generation(session, actor.project_id, generation_id, locked=True)
    if generation.created_by != actor.id:
        raise Forbidden("Only the creator of a generation can save it as a test case")
    if generation.status is not GenerationStatus.COMPLETED:
        raise Conflict("This generation was already saved as a test case")
    result = generation.result
    name = (result.get("scenario_ir") or {}).get("name") or f"generation_{generation.id}"
    case = await create_test_case(
        session,
        actor,
        TestCaseContent(
            title=body.title,
            description=body.description,
            map_code=body.map_code,
            ego_vehicle_code=body.ego_vehicle_code,
            adversary_type=body.adversary_type,
            environment_code=body.environment_code,
            danger_level=body.danger_level,
            # Provenance travels with the case: what was asked, against which CARLA data, and what came out.
            scenario_input={
                "source": "AGENT",
                "generation_id": generation.id,
                "prompt": generation.prompt,
                "generation_mode": generation.generation_mode,
                "model": generation.model,
                "catalog": result.get("catalog"),
                "scenario_ir": result.get("scenario_ir"),
                "form": result.get("form"),
                "grounding": result.get("grounding"),
                "validation": result.get("validation"),
                "threat_score": result.get("threat_score"),
                "warnings": result.get("warnings", []),
            },
            tag_names=body.tag_names,
        ),
        xosc=(generation.xosc.encode("utf-8"), f"{name}.xosc"),
        catalog_snapshot_id=generation.catalog_snapshot_id,
        builder_session_id=builder_session_id,
        builder_variant_no=builder_variant_no,
    )
    generation.status = GenerationStatus.ACCEPTED
    generation.accepted_test_case_id = case.id
    generation.accepted_at = datetime.now(UTC)
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="SCENARIO_GENERATION_SAVED",
        entity_type="SCENARIO_GENERATION",
        entity_id=generation.id,
        after_data={"test_case_id": case.id},
    )
    return case
