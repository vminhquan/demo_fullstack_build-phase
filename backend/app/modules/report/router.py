from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.modules.odd.service import get_odd
from app.modules.report.service import build_report
from app.shared.config import get_settings
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import AgentCall, ScenarioGeneration, TestCase

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/generation")
async def generation_report(
    days: int = Query(default=30, ge=1, le=365),
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Valid-scenario rate, failure reasons, review outcome, danger, ODD coverage and LLM cost over the last `days`."""
    since = datetime.now(UTC) - timedelta(days=days)
    calls = (await session.scalars(
        select(AgentCall).where(AgentCall.project_id == actor.project_id, AgentCall.created_at >= since)
    )).all()
    generations = (await session.scalars(
        select(ScenarioGeneration).where(ScenarioGeneration.project_id == actor.project_id, ScenarioGeneration.created_at >= since)
    )).all()
    versions = (await session.scalars(
        select(TestCase).where(
            TestCase.project_id == actor.project_id,
            TestCase.created_at >= since,
            TestCase.scenario_input["source"].astext == "AGENT",
        )
    )).all()
    odd = await get_odd(session, actor.project_id)
    declared = None
    if odd:
        d = odd.declaration
        declared = {"road_type": d["road_types"], "weather": d["weather"], "lighting": d["lighting"], "adversary_type": d["adversary_types"]}
    settings = get_settings()
    report = build_report(
        [{"kind": c.kind, "status": c.status, "error_code": c.error_code, "from_form": c.from_form, "cache_hit": c.cache_hit,
          "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "duration_ms": c.duration_ms,
          "day": c.created_at.date().isoformat()} for c in calls],
        [{"result": g.result, "generation_mode": g.generation_mode, "day": g.created_at.date().isoformat()} for g in generations],
        [{"status": v.status.value, "scenario_ir": (v.scenario_input or {}).get("scenario_ir")} for v in versions],
        declared,
        settings.llm_price_input_per_mtok,
        settings.llm_price_output_per_mtok,
    )
    return {"days": days, **report}
