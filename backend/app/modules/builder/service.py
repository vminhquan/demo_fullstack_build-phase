"""Test Case Builder sessions: one description + picked maps -> TARGET_COUNT Agent runs, each saved as a PENDING test case.

The run happens in the background (each Agent call takes seconds); the session row tracks progress and errors.
Every variant reuses the normal generation flow (create_generation + save_generation_as_case), so provenance, XOSC
upload and audit stay identical to a single generation.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.builder.schemas import BuilderMapOptions, BuilderSessionCreate
from app.modules.generation.agent_client import AgentPort
from app.modules.generation.router import create_generation, save_generation_as_case
from app.modules.generation.schemas import GenerationAccept, GenerationCreate, GenerationMetadata
from app.modules.identity.dependencies import Principal
from app.shared.domain.errors import DomainError, NotFound
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import SessionFactory
from app.shared.infrastructure.models import BuilderSession, BuilderSessionStatus, TestCase

log = logging.getLogger("scenario_forge.builder")

# Every session asks the Agent for this many test cases, spread over the picked maps.
TARGET_COUNT = 10
MAX_PARALLEL = 3
TITLE_MAX_WORDS = 12  # Vietnamese words are syllables

# Background runs kept referenced so they are not garbage-collected mid-flight.
_running: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class Variant:
    no: int  # 1..TARGET_COUNT
    map_code: str
    ego_vehicle_code: str | None
    adversary_type: str | None
    environment_code: str | None
    danger_level: str | None


def plan_variants(maps: list[BuilderMapOptions], target: int = TARGET_COUNT) -> list[Variant]:
    """Variant i goes to map i mod len(maps); the k-th variant of a map takes the k-th ticked value of each category."""
    variants = []
    for index in range(target):
        options = maps[index % len(maps)]
        slot = index // len(maps)

        def pick(values: list[Any]) -> Any:
            return values[slot % len(values)] if values else None

        danger = pick(options.danger_levels)
        variants.append(Variant(
            no=index + 1,
            map_code=options.map_code,
            ego_vehicle_code=pick(options.ego_vehicle_codes),
            adversary_type=pick(options.adversary_types),
            environment_code=pick(options.environment_codes),
            danger_level=getattr(danger, "value", danger),
        ))
    return variants


def fallback_title(description: str) -> str:
    """First clause of the description, a few words long (used until/unless the Agent summarises it)."""
    clause = re.split(r"[.\n;:,!?]", description.strip(), maxsplit=1)[0].strip() or description.strip()
    title = " ".join(clause.split()[:TITLE_MAX_WORDS])
    return (title[:1].upper() + title[1:])[:80] or "Kịch bản mới"


def variant_prompt(description: str, no: int, total: int) -> str:
    # Ask each variant for different numbers; the seed also moves the ego start on the map.
    return f"{description}\nBiến thể {no}/{total}: chọn tốc độ, khoảng cách và thời điểm kích hoạt khác các biến thể còn lại."


def case_title(builder: BuilderSession, variant: Variant) -> str:
    suffix = f" (#{variant.no})" + (f" · {variant.map_code}" if len(builder.maps) > 1 else "")
    return f"{builder.title[: 300 - len(suffix)]}{suffix}"


async def create_session(session: AsyncSession, actor: Principal, body: BuilderSessionCreate) -> BuilderSession:
    typed = (body.title or "").strip()
    builder = BuilderSession(
        project_id=actor.project_id,
        created_by=actor.id,
        title=typed or fallback_title(body.description),
        title_source="USER" if typed else "AUTO",
        prompt=body.description.strip(),
        catalog_source=body.catalog_source,
        maps=[item.model_dump(mode="json") for item in body.maps],
        tag_names=[tag.strip() for tag in body.tag_names if tag.strip()],
        target_count=TARGET_COUNT,
        status=BuilderSessionStatus.GENERATING,
    )
    session.add(builder)
    await session.flush()
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="BUILDER_SESSION_CREATED",
        entity_type="BUILDER_SESSION",
        entity_id=builder.id,
        after_data={"maps": [item.map_code for item in body.maps], "target_count": TARGET_COUNT},
    )
    await session.commit()
    return builder


def start_run(builder_id: int, actor: Principal, agent: AgentPort) -> None:
    task = asyncio.create_task(run_session(builder_id, actor, agent))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def run_session(builder_id: int, actor: Principal, agent: AgentPort) -> None:
    try:
        async with SessionFactory() as session:
            builder = await session.get(BuilderSession, builder_id)
            if builder is None:
                return
            if builder.title_source == "AUTO":
                # Like a chat app naming a new conversation; keep the fallback title if the Agent cannot answer.
                try:
                    reply = await agent.title(description=builder.prompt)
                    if str(reply.get("title") or "").strip():
                        builder.title = str(reply["title"]).strip()[:300]
                        await session.commit()
                except DomainError as exc:
                    log.warning("Session title fell back to the description: %s", exc.message)
            maps = [BuilderMapOptions(**item) for item in builder.maps]
            variants = plan_variants(maps, builder.target_count)
            await session.refresh(builder)

        limit = asyncio.Semaphore(MAX_PARALLEL)

        async def guarded(variant: Variant) -> None:
            async with limit:
                await run_variant(builder_id, builder, actor, agent, variant)

        await asyncio.gather(*(guarded(variant) for variant in variants))
    except Exception:  # noqa: BLE001 - the session must never stay GENERATING forever
        log.exception("Builder session %s crashed", builder_id)
    finally:
        await finish_session(builder_id)


async def run_variant(builder_id: int, builder: BuilderSession, actor: Principal, agent: AgentPort, variant: Variant) -> None:
    async with SessionFactory() as session:
        try:
            generation = await create_generation(
                GenerationCreate(
                    prompt=variant_prompt(builder.prompt, variant.no, builder.target_count),
                    catalog_source=builder.catalog_source,
                    seed=variant.no,
                    # The map pins the CARLA data; each ticked value constrains the Agent, the rest is its choice.
                    metadata=GenerationMetadata(
                        map_code=variant.map_code,
                        ego_vehicle_code=variant.ego_vehicle_code,
                        adversary_type=variant.adversary_type,
                        environment_code=variant.environment_code,
                    ),
                    use_cache=False,
                ),
                actor, session, agent,
            )
            suggested = generation.suggested_metadata
            # Saved straight into PENDING: generated once, reviewers approve or reject, no draft step.
            await save_generation_as_case(
                generation.id,
                GenerationAccept(
                    title=case_title(builder, variant),
                    description=builder.prompt,
                    map_code=variant.map_code,
                    ego_vehicle_code=variant.ego_vehicle_code or suggested.ego_vehicle_code,
                    adversary_type=variant.adversary_type or suggested.adversary_type,
                    environment_code=variant.environment_code or suggested.environment_code,
                    danger_level=variant.danger_level or suggested.danger_level,
                    tag_names=builder.tag_names or suggested.tag_names,
                ),
                actor, session,
                builder_session_id=builder_id,
                builder_variant_no=variant.no,
            )
            await session.execute(
                update(BuilderSession)
                .where(BuilderSession.id == builder_id)
                .values(succeeded_count=BuilderSession.succeeded_count + 1)
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - one failed variant must not stop the others
            await session.rollback()
            code = getattr(exc, "code", None) or "GENERATION_FAILED"
            message = exc.message if isinstance(exc, DomainError) else f"{type(exc).__name__}"
            if not isinstance(exc, DomainError):
                log.exception("Builder session %s variant %s failed", builder_id, variant.no)
            await record_error(builder_id, {"variant_no": variant.no, "map_code": variant.map_code, "code": str(code), "message": message})


async def record_error(builder_id: int, error: dict[str, Any]) -> None:
    async with SessionFactory() as session:
        # Row lock: parallel variants append to the same JSON list.
        builder = await session.scalar(select(BuilderSession).where(BuilderSession.id == builder_id).with_for_update())
        if builder is None:
            return
        builder.errors = [*builder.errors, error]
        builder.failed_count += 1
        await session.commit()


async def finish_session(builder_id: int) -> None:
    async with SessionFactory() as session:
        builder = await session.scalar(select(BuilderSession).where(BuilderSession.id == builder_id).with_for_update())
        if builder is None:
            return
        if builder.succeeded_count == 0:
            builder.status = BuilderSessionStatus.FAILED
        elif builder.succeeded_count < builder.target_count:
            builder.status = BuilderSessionStatus.PARTIAL
        else:
            builder.status = BuilderSessionStatus.COMPLETED
        builder.finished_at = datetime.now(UTC)
        await session.commit()


async def fail_interrupted_sessions() -> None:
    """Runs live in the API process; after a restart, sessions still GENERATING can never finish."""
    async with SessionFactory() as session:
        await session.execute(
            update(BuilderSession)
            .where(BuilderSession.status == BuilderSessionStatus.GENERATING)
            .values(status=BuilderSessionStatus.FAILED, finished_at=datetime.now(UTC))
        )
        await session.commit()


async def get_session_row(session: AsyncSession, project_id: int, builder_id: int) -> BuilderSession:
    builder = await session.scalar(
        select(BuilderSession).where(BuilderSession.id == builder_id, BuilderSession.project_id == project_id)
    )
    if builder is None:
        raise NotFound("Builder session not found")
    return builder


async def session_cases(session: AsyncSession, project_id: int, builder_id: int) -> list[TestCase]:
    return list((await session.scalars(
        select(TestCase)
        .where(TestCase.project_id == project_id, TestCase.builder_session_id == builder_id)
        .order_by(TestCase.builder_variant_no, TestCase.id)
    )).all())
