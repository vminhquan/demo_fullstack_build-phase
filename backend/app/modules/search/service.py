from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.search.schemas import (
    RagSearchFilters,
    RagSearchHit,
    RagSearchRequest,
    RagSearchResponse,
    SimulationSummary,
)
from app.shared.config import get_settings
from app.shared.domain.errors import ValidationFailed
from app.shared.infrastructure.models import (
    DangerLevel,
    RunJob,
    RunResult,
    RunVerdict,
    TestCase,
    TestCaseSearchDocument,
    TestCaseStatus,
)


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


def tokens(value: str) -> set[str]:
    return {token for token in normalize(value).split() if len(token) > 1}


def infer_filters(prompt: str, explicit: RagSearchFilters) -> RagSearchFilters:
    value = normalize(prompt)
    # Match whole words/phrases only: substring checks turned "dam bao" (ensure) or "damage" into "dam" (crash).
    words = f" {value} ".replace(" dam bao ", " ")

    def has(*terms: str) -> bool:
        return any(f" {term} " in words for term in terms)

    filters = explicit.model_copy(deep=True)
    if not filters.map_code:
        filters.map_code = [f"Town{number}" for number in re.findall(r"town\s*(\d+)", value)]
    if not filters.adversary_type:
        if has("xe may", "motorcycle", "motorbike"):
            filters.adversary_type = ["motorcycle"]
        elif has("nguoi di bo", "pedestrian"):
            filters.adversary_type = ["pedestrian"]
        elif has("xe dap", "cyclist", "bicycle"):
            filters.adversary_type = ["cyclist"]
        elif has("xe phia truoc", "xe nguoc chieu", "truck", "vehicle"):
            filters.adversary_type = ["vehicle"]
    if not filters.environment_code:
        if has("mua to", "mua lon", "heavy rain"):
            filters.environment_code = ["heavy_rain"]
        elif has("mua", "rain", "wet road", "duong uot"):
            filters.environment_code = ["heavy_rain", "wet_cloudy"]
        elif has("ban dem", "troi toi", "night", "dark"):
            filters.environment_code = ["night"]
        elif has("suong mu", "fog"):
            filters.environment_code = ["fog"]
    if not filters.danger_level:
        if has("nghiem trong", "critical"):
            filters.danger_level = [DangerLevel.CRITICAL]
        elif has("nguy hiem cao", "high risk", "dangerous"):
            filters.danger_level = [DangerLevel.HIGH, DangerLevel.CRITICAL]
    if not filters.status:
        filters.status = [TestCaseStatus.APPROVED]
    if not filters.verdict and has("that bai", "fail", "failed", "va cham", "collision", "dam"):
        filters.verdict = [RunVerdict.FAIL]
    if has("va cham", "collision", "dam"):
        filters.collision_only = True
    return filters


def database_result_summary(result: RunResult | None) -> SimulationSummary | None:
    if result is None:
        return None
    metrics = result.metrics or {}
    collision = bool(metrics.get("collision", False)) or int(
        metrics.get("collision_count", 0)
    ) > 0
    min_ttc = metrics.get("min_ttc_seconds")
    return SimulationSummary(
        run_result_id=result.id,
        run_job_id=result.run_job_id,
        verdict=result.verdict,
        collision=collision,
        min_ttc_seconds=float(min_ttc) if isinstance(min_ttc, int | float) else None,
        duration_ms=result.duration_ms,
        metrics=metrics,
    )


def database_matches(
    version: TestCase,
    tag_names: set[str],
    result: RunResult | None,
    filters: RagSearchFilters,
) -> bool:
    simulation = database_result_summary(result)
    return (
        (not filters.map_code or version.map_code in filters.map_code)
        and (not filters.adversary_type or version.adversary_type in filters.adversary_type)
        and (
            not filters.environment_code
            or version.environment_code in filters.environment_code
        )
        and (not filters.danger_level or version.danger_level in filters.danger_level)
        and (not filters.status or version.status in filters.status)
        and (not filters.tag or {tag.lower() for tag in filters.tag}.issubset(tag_names))
        and (
            not filters.verdict
            or (simulation is not None and simulation.verdict in filters.verdict)
        )
        and (
            not filters.collision_only
            or (simulation is not None and simulation.collision)
        )
    )


def database_score(
    prompt: str,
    case: TestCase,
    version: TestCase,
    tag_names: set[str],
    search_text: str,
    result: RunResult | None,
    filters: RagSearchFilters,
) -> float:
    metrics = result.metrics if result is not None else {}
    searchable = " ".join(
        [
            search_text,
            case.title,
            case.description or "",
            version.map_code,
            version.adversary_type,
            version.environment_code,
            " ".join(tag_names),
            json.dumps(version.scenario_input, ensure_ascii=False),
            json.dumps(metrics, ensure_ascii=False),
        ]
    )
    prompt_tokens, case_tokens = tokens(prompt), tokens(searchable)
    overlap = len(prompt_tokens & case_tokens) / max(len(prompt_tokens), 1)
    score = 0.2 + 0.6 * overlap
    score += 0.06 if filters.adversary_type else 0
    score += 0.05 if filters.environment_code else 0
    score += 0.04 if filters.map_code else 0
    score += 0.03 if filters.verdict else 0
    score += 0.02 if filters.collision_only else 0
    return round(min(score, 0.99), 4)


async def search_postgres(
    body: RagSearchRequest, session: AsyncSession, project_id: int
) -> RagSearchResponse:
    filters = infer_filters(body.prompt, body.filters)
    # One row per test case now (no versions); `version` below is the case itself.
    versions = (
        await session.scalars(
            select(TestCase).where(
                TestCase.project_id == project_id,
                TestCase.archived_at.is_(None),
                TestCase.status != TestCaseStatus.DISCARDED,
            )
        )
    ).all()
    version_ids = [version.id for version in versions]
    latest_results: dict[Any, RunResult] = {}
    documents: dict[Any, str] = {}
    if version_ids:
        result_rows = await session.execute(
            select(RunResult, RunJob)
            .join(RunJob, RunResult.run_job_id == RunJob.id)
            .where(RunJob.test_case_id.in_(version_ids))
            .order_by(RunJob.finished_at.desc().nullslast(), RunResult.created_at.desc())
        )
        for result, job in result_rows.tuples():
            latest_results.setdefault(job.test_case_id, result)
        document_rows = await session.execute(
            select(
                TestCaseSearchDocument.test_case_id,
                TestCaseSearchDocument.search_text,
            ).where(
                TestCaseSearchDocument.project_id == project_id,
                TestCaseSearchDocument.test_case_id.in_(version_ids),
            )
        )
        documents = dict(document_rows.tuples().all())

    hits: list[RagSearchHit] = []
    for version in versions:
        case = version
        tag_names = {tag.name.lower() for tag in version.tags}
        result = latest_results.get(version.id)
        if not database_matches(version, tag_names, result, filters):
            continue
        simulation = database_result_summary(result)
        reasons = ["Khớp từ khóa và taxonomy trên dữ liệu PostgreSQL"]
        if filters.map_code:
            reasons.append(f"Map: {version.map_code}")
        if filters.adversary_type:
            reasons.append(f"Tác nhân: {version.adversary_type}")
        if filters.environment_code:
            reasons.append(f"Môi trường: {version.environment_code}")
        if filters.verdict and simulation is not None:
            reasons.append(f"CARLA verdict: {simulation.verdict.value}")
        if filters.collision_only:
            reasons.append("CARLA ghi nhận collision")
        hits.append(
            RagSearchHit(
                case_id=case.id,
                case_key=case.case_key,
                revision=version.revision,
                title=case.title,
                status=version.status,
                map_code=version.map_code,
                adversary_type=version.adversary_type,
                environment_code=version.environment_code,
                danger_level=version.danger_level,
                tags=sorted(tag_names),
                score=database_score(
                    body.prompt,
                    case,
                    version,
                    tag_names,
                    documents.get(version.id, ""),
                    result,
                    filters,
                ),
                matched_by=["postgres", "keyword", "taxonomy"],
                match_reasons=reasons,
                simulation=simulation,
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return RagSearchResponse(
        prompt=body.prompt,
        interpreted_filters=filters,
        items=hits[: body.limit],
        total=len(hits),
        retrieval_mode="postgres_keyword_taxonomy",
        semantic_fallback=True,
    )


async def search(
    body: RagSearchRequest, session: AsyncSession, project_id: int
) -> RagSearchResponse:
    if get_settings().rag_data_source.lower() != "postgres":
        raise ValidationFailed("RAG_DATA_SOURCE must be postgres")
    return await search_postgres(body, session, project_id)
