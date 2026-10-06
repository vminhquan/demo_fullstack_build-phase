from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, current_principal, require
from app.modules.testcase.schemas import (
    BatchDecisionRequest,
    BatchDiscardRequest,
    BatchItemResult,
    BatchResponse,
    DecisionRequest,
    DecisionResponse,
    LockRequest,
    LockResponse,
    PageResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    TestCaseContent,
    TestCaseResponse,
    TestCaseUpdate,
    UploadResponse,
)
from app.modules.testcase.service import (
    case_response,
    case_responses,
    create_test_case,
    decide_test_case,
    discard_test_case,
    edit_test_case,
    get_case,
    lock_for_run,
    replace_xosc,
    restore_test_case,
    undo_decision,
    user_names,
)
from app.shared.config import get_settings
from app.shared.domain.errors import DomainError, ValidationFailed
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.http import content_disposition
from app.shared.infrastructure.models import (
    Artifact,
    Tag,
    TestCase,
    TestCaseDecision,
    TestCaseDecisionValue,
    TestCaseStatus,
    test_case_tags,
)

router = APIRouter(prefix="/test-cases", tags=["test-cases"])
decisions_router = APIRouter(prefix="/test-case-decisions", tags=["test-cases"])


async def respond(session: AsyncSession, case: TestCase, actor: Principal) -> TestCaseResponse:
    await session.refresh(case)
    return (await case_responses(session, [case], actor))[0]


@router.post("", response_model=TestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: TestCaseContent,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    """Hand-written case (generated cases come from Test Case Builder); goes straight to PENDING."""
    case = await create_test_case(session, actor, body)
    await session.commit()
    return await respond(session, case, actor)


@router.get("", response_model=PageResponse)
async def list_cases(
    map_code: str | None = None,
    adversary_type: str | None = None,
    environment_code: str | None = None,
    danger_level: str | None = None,
    status_value: list[TestCaseStatus] | None = Query(default=None, alias="status"),
    # Kept for the Test Suite screen: same as status=APPROVED.
    approved_only: bool = False,
    include_discarded: bool = False,
    builder_session_id: int | None = None,
    creator_id: int | None = None,
    tag: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> PageResponse:
    conditions = [TestCase.project_id == actor.project_id, TestCase.archived_at.is_(None)]
    statuses = [TestCaseStatus.APPROVED] if approved_only else (status_value or [])
    if statuses:
        conditions.append(TestCase.status.in_(statuses))
    elif not include_discarded:
        # Discarded cases are the creator's own clean-up; they show only when asked for.
        conditions.append(TestCase.status != TestCaseStatus.DISCARDED)
    for column, value in (
        (TestCase.map_code, map_code),
        (TestCase.adversary_type, adversary_type),
        (TestCase.environment_code, environment_code),
        (TestCase.danger_level, danger_level),
        (TestCase.builder_session_id, builder_session_id),
        (TestCase.created_by, creator_id),
    ):
        if value is not None and value != "":
            conditions.append(column == value)
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(or_(TestCase.title.ilike(pattern), TestCase.description.ilike(pattern), TestCase.case_key.ilike(pattern)))
    if tag:
        conditions.append(
            TestCase.id.in_(
                select(test_case_tags.c.test_case_id)
                .join(Tag, Tag.id == test_case_tags.c.tag_id)
                .where(func.lower(Tag.name) == tag.strip().lower())
            )
        )
    total = int(await session.scalar(select(func.count()).select_from(TestCase).where(*conditions)) or 0)
    order = (TestCase.builder_variant_no, TestCase.id) if builder_session_id else (TestCase.updated_at.desc(), TestCase.id.desc())
    cases = list((await session.scalars(
        select(TestCase).where(*conditions).order_by(*order).offset((page - 1) * page_size).limit(page_size)
    )).all())
    return PageResponse(items=await case_responses(session, cases, actor), page=page, page_size=page_size, total=total)


@router.post("/search", response_model=SearchResponse)
async def search_cases(
    body: SearchRequest,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    cases = (await session.scalars(
        select(TestCase).where(
            TestCase.project_id == actor.project_id,
            TestCase.archived_at.is_(None),
            TestCase.status != TestCaseStatus.DISCARDED,
        )
    )).all()
    filters = body.filters
    words = {word for word in body.query.lower().split() if word}
    hits: list[SearchHit] = []
    for case in cases:
        tags = {tag_item.name.lower() for tag_item in case.tags}
        if (
            (filters.map_code and case.map_code not in filters.map_code)
            or (filters.adversary_type and case.adversary_type not in filters.adversary_type)
            or (filters.environment_code and case.environment_code not in filters.environment_code)
            or (filters.danger_level and case.danger_level not in filters.danger_level)
            or (filters.status and case.status not in filters.status)
            or (filters.creator_id and case.created_by not in filters.creator_id)
            or (filters.tag and not {value.lower() for value in filters.tag}.issubset(tags))
        ):
            continue
        haystack = f"{case.title} {case.description or ''} {case.adversary_type} {case.environment_code} {' '.join(tags)}".lower()
        score = sum(word in haystack for word in words) / len(words) if words else None
        if words and score == 0 and body.mode in {"keyword", "hybrid"}:
            continue
        hits.append(SearchHit(
            case_id=case.id, case_key=case.case_key, revision=case.revision, title=case.title, status=case.status,
            map_code=case.map_code, adversary_type=case.adversary_type, environment_code=case.environment_code,
            danger_level=case.danger_level, tags=sorted(tags), score=score,
            matched_by=["filter"] + (["keyword"] if words else []),
        ))
    hits.sort(key=lambda hit: (hit.score is not None, hit.score or 0, hit.case_id), reverse=True)
    start = (body.page - 1) * body.page_size
    fallback = body.mode in {"semantic", "hybrid"} and get_settings().embedding_provider == "disabled"
    return SearchResponse(items=hits[start : start + body.page_size], page=body.page, page_size=body.page_size,
                          total=len(hits), semantic_fallback=fallback)


# Batch and lock routes are declared before /{case_id} so "batch"/"lock" are never parsed as an id.
@router.post("/batch/decision", response_model=BatchResponse)
async def batch_decide(
    body: BatchDecisionRequest,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> BatchResponse:
    """Each case is decided in its own transaction; one refusal does not stop the others."""
    results = []
    for case_id in dict.fromkeys(body.ids):
        try:
            case = await get_case(session, actor.project_id, case_id, locked=True)
            await decide_test_case(session, case, actor, body.decision)
            await session.commit()
            results.append(BatchItemResult(id=case_id, status=case.status))
        except DomainError as exc:
            await session.rollback()
            results.append(BatchItemResult(id=case_id, error=exc.details.get("code") or exc.code, message=exc.message))
    return BatchResponse(results=results)


@router.post("/batch/discard", response_model=BatchResponse)
async def batch_discard(
    body: BatchDiscardRequest,
    actor: Principal = Depends(require("testcase:discard")),
    session: AsyncSession = Depends(get_session),
) -> BatchResponse:
    results = []
    for case_id in dict.fromkeys(body.ids):
        try:
            case = await get_case(session, actor.project_id, case_id, locked=True)
            await discard_test_case(session, case, actor)
            await session.commit()
            results.append(BatchItemResult(id=case_id, status=case.status))
        except DomainError as exc:
            await session.rollback()
            results.append(BatchItemResult(id=case_id, error=exc.details.get("code") or exc.code, message=exc.message))
    return BatchResponse(results=results)


@router.post("/lock", response_model=LockResponse)
async def lock_cases(
    body: LockRequest,
    actor: Principal = Depends(require("suite:run")),
    session: AsyncSession = Depends(get_session),
) -> LockResponse:
    """Called when cases are put into a simulator run: they become read-only for good."""
    runnable, skipped = await lock_for_run(session, actor.project_id, body.ids, actor.id)
    await session.commit()
    return LockResponse(
        locked=[case.id for case in runnable],
        skipped=[BatchItemResult(id=case_id, error=reason) for case_id, reason in skipped],
    )


@router.get("/{case_id}", response_model=TestCaseResponse)
async def read_case(
    case_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id)
    return (await case_responses(session, [case], actor))[0]


@router.patch("/{case_id}", response_model=TestCaseResponse)
async def patch_case(
    case_id: int,
    body: TestCaseUpdate,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    await edit_test_case(session, case, body, actor)
    await session.commit()
    return await respond(session, case, actor)


@router.post("/{case_id}/xosc", response_model=UploadResponse)
async def post_xosc(
    case_id: int,
    file: UploadFile = File(...),
    expected_revision: int | None = Form(default=None),
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    content = await file.read(get_settings().max_xosc_size_bytes + 1)
    if len(content) > get_settings().max_xosc_size_bytes:
        raise ValidationFailed("XOSC file exceeds configured size limit")
    case = await get_case(session, actor.project_id, case_id, locked=True)
    artifact = await replace_xosc(session, case, actor, content, file.filename or "scenario.xosc",
                                  file.content_type or "application/xml", expected_revision)
    await session.commit()
    return UploadResponse(artifact_id=artifact.id, sha256=artifact.sha256, size_bytes=artifact.size_bytes)


@router.get("/{case_id}/xosc")
async def get_xosc(
    case_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    case = await get_case(session, actor.project_id, case_id)
    artifact = await session.get(Artifact, case.xosc_artifact_id) if case.xosc_artifact_id else None
    if artifact is None:
        raise ValidationFailed("Test case has no XOSC file")
    return Response(
        artifact.content,
        media_type=artifact.content_type or "application/xml",
        headers={"Content-Disposition": content_disposition(f"{case.case_key}.xosc")},
    )


@router.post("/{case_id}/decision", response_model=TestCaseResponse)
async def decide(
    case_id: int,
    body: DecisionRequest,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    await decide_test_case(session, case, actor, body.decision, body.expected_revision)
    await session.commit()
    return await respond(session, case, actor)


@router.post("/{case_id}/decision/undo", response_model=TestCaseResponse)
async def undo(
    case_id: int,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    await undo_decision(session, case, actor)
    await session.commit()
    return await respond(session, case, actor)


@router.post("/{case_id}/discard", response_model=TestCaseResponse)
async def discard(
    case_id: int,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    await discard_test_case(session, case, actor)
    await session.commit()
    return await respond(session, case, actor)


@router.post("/{case_id}/restore", response_model=TestCaseResponse)
async def restore(
    case_id: int,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    await restore_test_case(session, case, actor)
    await session.commit()
    return await respond(session, case, actor)


async def decision_responses(session: AsyncSession, rows: list[TestCaseDecision]) -> list[DecisionResponse]:
    names = await user_names(session, [row.decided_by for row in rows])
    cases = {
        case.id: case
        for case in (await session.scalars(select(TestCase).where(TestCase.id.in_({row.test_case_id for row in rows})))).all()
    } if rows else {}
    return [
        DecisionResponse(
            id=row.id, test_case_id=row.test_case_id,
            case_key=cases[row.test_case_id].case_key if row.test_case_id in cases else None,
            title=cases[row.test_case_id].title if row.test_case_id in cases else None,
            decision=row.decision, revision=row.revision, config_sha256=row.config_sha256,
            decided_by=row.decided_by, decided_by_name=names.get(row.decided_by),
            created_at=row.created_at, undone_at=row.undone_at,
        )
        for row in rows
    ]


@router.get("/{case_id}/decisions", response_model=list[DecisionResponse])
async def case_decisions(
    case_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionResponse]:
    await get_case(session, actor.project_id, case_id)
    rows = list((await session.scalars(
        select(TestCaseDecision)
        .where(TestCaseDecision.project_id == actor.project_id, TestCaseDecision.test_case_id == case_id)
        .order_by(TestCaseDecision.created_at.desc(), TestCaseDecision.id.desc())
    )).all())
    return await decision_responses(session, rows)


@decisions_router.get("", response_model=list[DecisionResponse])
async def list_decisions(
    decision: TestCaseDecisionValue | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionResponse]:
    """Decision history of the project, newest first (undone decisions are listed with undone_at)."""
    statement = (
        select(TestCaseDecision)
        .where(TestCaseDecision.project_id == actor.project_id)
        .order_by(TestCaseDecision.created_at.desc(), TestCaseDecision.id.desc())
        .limit(limit)
    )
    if decision is not None:
        statement = statement.where(TestCaseDecision.decision == decision)
    return await decision_responses(session, list((await session.scalars(statement)).all()))


__all__ = ["router", "decisions_router", "case_response"]
