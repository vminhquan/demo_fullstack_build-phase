from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.generation.agent_client import AgentPort, get_agent
from app.modules.identity.dependencies import Principal, require
from app.modules.odd.schemas import OddCheckResponse, OddDeclaration, OddResponse, PlanRequest, PlanResponse
from app.modules.odd.planner import plan_cases
from app.modules.odd.service import capabilities, get_odd, odd_gaps
from app.shared.domain.errors import ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import OddProfile

router = APIRouter(prefix="/odd-profile", tags=["odd"])


def _optional_agent() -> AgentPort | None:
    try:
        return get_agent()
    except Exception:  # capabilities still list snapshots; profiles fill in once the Agent is reachable
        return None


@router.get("", response_model=OddResponse)
async def read_odd(
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort | None = Depends(_optional_agent),
) -> OddResponse:
    caps = await capabilities(session, actor.project_id, agent)
    profile = await get_odd(session, actor.project_id)
    declaration = OddDeclaration(**profile.declaration) if profile else None
    return OddResponse(
        declaration=declaration,
        updated_at=profile.updated_at if profile else None,
        updated_by=profile.updated_by if profile else None,
        capabilities=caps,
        gaps=odd_gaps(declaration, caps) if declaration else [],
    )


@router.post("/check", response_model=OddCheckResponse)
async def check_odd(
    body: OddDeclaration,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort | None = Depends(_optional_agent),
) -> OddCheckResponse:
    """Gaps of an unsaved declaration (live feedback while editing)."""
    return OddCheckResponse(gaps=odd_gaps(body, await capabilities(session, actor.project_id, agent)))


@router.put("", response_model=OddResponse)
async def save_odd(
    body: OddDeclaration,
    actor: Principal = Depends(require("odd:manage")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort | None = Depends(_optional_agent),
) -> OddResponse:
    caps = await capabilities(session, actor.project_id, agent)
    visible = {cap.snapshot_id for cap in caps}
    unknown = [item for item in body.catalog_snapshot_ids if item not in visible]
    if unknown:
        raise ValidationFailed(f"CARLA data not found in this project: {unknown}")
    profile = await get_odd(session, actor.project_id)
    before = profile.declaration if profile else None
    if profile is None:
        profile = OddProfile(project_id=actor.project_id, declaration={})
        session.add(profile)
    profile.declaration = body.model_dump(mode="json")
    profile.updated_by = actor.id
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="ODD_UPDATED",
        entity_type="ODD_PROFILE",
        entity_id=actor.project_id,
        before_data=before,
        after_data=profile.declaration,
    )
    await session.commit()
    await session.refresh(profile)
    return OddResponse(declaration=body, updated_at=profile.updated_at, updated_by=profile.updated_by,
                       capabilities=caps, gaps=odd_gaps(body, caps))


@router.post("/plan", response_model=PlanResponse)
async def plan(
    body: PlanRequest,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
    agent: AgentPort | None = Depends(_optional_agent),
) -> PlanResponse:
    """Spread `count` cases over the form's ODD values (pairwise) and ranges (strata); nothing is stored."""
    caps = await capabilities(session, actor.project_id, agent)
    profile = await get_odd(session, actor.project_id)
    return plan_cases(body.form, body.count, caps, OddDeclaration(**profile.declaration) if profile else None)
