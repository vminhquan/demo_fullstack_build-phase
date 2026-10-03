from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import Principal, current_principal, require
from app.modules.testcase.schemas import (
    PageResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    TestCaseCreate,
    TestCaseResponse,
    TestCaseUpdate,
    UploadResponse,
    VersionPayload,
    VersionResponse,
)
from app.modules.testcase.service import (
    case_response,
    create_version,
    get_case,
    get_version,
    update_version,
    upload_xosc,
    version_response,
)
from app.shared.config import get_settings
from app.shared.domain.errors import Forbidden, ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.http import content_disposition
from app.shared.infrastructure.models import TestCase, TestCaseVersion

router = APIRouter(prefix="/test-cases", tags=["test-cases"])
versions_router = APIRouter(prefix="/test-case-versions", tags=["test-case-versions"])


@router.post("", response_model=TestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: TestCaseCreate,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = TestCase(
        project_id=actor.project_id,
        case_key=f"TEMP-{secrets.token_hex(8).upper()}",
        title=body.title,
        description=body.description,
        created_by=actor.id,
        versions=[],
    )
    session.add(case)
    await session.flush()
    case.case_key = f"TC-{case.id:06d}"
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_CREATED",
        entity_type="TEST_CASE",
        entity_id=case.id,
    )
    await session.commit()
    return case_response(case)


@router.get("", response_model=PageResponse)
async def list_cases(
    map_code: str | None = None,
    adversary_type: str | None = None,
    environment_code: str | None = None,
    danger_level: str | None = None,
    status_value: str | None = Query(default=None, alias="status"),
    creator_id: int | None = None,
    tag: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> PageResponse:
    cases = (
        await session.scalars(
            select(TestCase)
            .where(
                TestCase.project_id == actor.project_id,
                TestCase.archived_at.is_(None),
            )
            .options(selectinload(TestCase.versions).selectinload(TestCaseVersion.tags))
            .order_by(TestCase.updated_at.desc())
        )
    ).all()

    def matches(case: TestCase) -> bool:
        if creator_id and case.created_by != creator_id:
            return False
        if q and q.lower() not in f"{case.title} {case.description or ''}".lower():
            return False
        latest = max(case.versions, key=lambda item: item.version_no) if case.versions else None
        if latest is None:
            return not any(
                [map_code, adversary_type, environment_code, danger_level, status_value, tag]
            )
        values = (
            (map_code, latest.map_code),
            (adversary_type, latest.adversary_type),
            (environment_code, latest.environment_code),
            (danger_level, latest.danger_level.value),
            (status_value, latest.status.value),
        )
        if any(expected and expected != actual for expected, actual in values):
            return False
        return not tag or tag.lower() in {item.name.lower() for item in latest.tags}

    filtered = [case for case in cases if matches(case)]
    start = (page - 1) * page_size
    return PageResponse(
        items=[case_response(case) for case in filtered[start : start + page_size]],
        page=page,
        page_size=page_size,
        total=len(filtered),
    )


@router.post("/search", response_model=SearchResponse)
async def search_cases(
    body: SearchRequest,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    cases = (
        await session.scalars(
            select(TestCase)
            .where(
                TestCase.project_id == actor.project_id,
                TestCase.archived_at.is_(None),
            )
            .options(selectinload(TestCase.versions).selectinload(TestCaseVersion.tags))
        )
    ).all()
    filters = body.filters
    words = {word for word in body.query.lower().split() if word}
    hits: list[SearchHit] = []
    for case in cases:
        for version in case.versions:
            if filters.map_code and version.map_code not in filters.map_code:
                continue
            if filters.adversary_type and version.adversary_type not in filters.adversary_type:
                continue
            if filters.environment_code and version.environment_code not in filters.environment_code:
                continue
            if filters.danger_level and version.danger_level not in filters.danger_level:
                continue
            if filters.status and version.status not in filters.status:
                continue
            if filters.creator_id and case.created_by not in filters.creator_id:
                continue
            tags = {tag_item.name.lower() for tag_item in version.tags}
            if filters.tag and not {value.lower() for value in filters.tag}.issubset(tags):
                continue
            haystack = (
                f"{case.title} {case.description or ''} {version.adversary_type} "
                f"{version.environment_code} {' '.join(tags)}"
            ).lower()
            score = sum(word in haystack for word in words) / len(words) if words else None
            if words and score == 0 and body.mode in {"keyword", "hybrid"}:
                continue
            hits.append(
                SearchHit(
                    case_id=case.id,
                    case_key=case.case_key,
                    version_id=version.id,
                    version_no=version.version_no,
                    title=case.title,
                    status=version.status,
                    map_code=version.map_code,
                    adversary_type=version.adversary_type,
                    environment_code=version.environment_code,
                    danger_level=version.danger_level,
                    tags=sorted(tags),
                    score=score,
                    matched_by=["filter"] + (["keyword"] if words else []),
                )
            )
    hits.sort(
        key=lambda hit: (hit.score is not None, hit.score or 0, hit.version_no), reverse=True
    )
    start = (body.page - 1) * body.page_size
    fallback = (
        body.mode in {"semantic", "hybrid"}
        and get_settings().embedding_provider == "disabled"
    )
    return SearchResponse(
        items=hits[start : start + body.page_size],
        page=body.page,
        page_size=body.page_size,
        total=len(hits),
        semantic_fallback=fallback,
    )


@router.get("/{case_id}", response_model=TestCaseResponse)
async def read_case(
    case_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    return case_response(await get_case(session, actor.project_id, case_id))


@router.patch("/{case_id}", response_model=TestCaseResponse)
async def patch_case(
    case_id: int,
    body: TestCaseUpdate,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    if case.created_by != actor.id:
        raise Forbidden("Only the test-case creator can edit its stable fields")
    before = {"title": case.title, "description": case.description}
    if body.title is not None:
        case.title = body.title
    if body.description is not None:
        case.description = body.description
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_UPDATED",
        entity_type="TEST_CASE",
        entity_id=case.id,
        before_data=before,
        after_data={"title": case.title, "description": case.description},
    )
    await session.commit()
    return case_response(case)


@router.post("/{case_id}/versions", response_model=VersionResponse, status_code=status.HTTP_201_CREATED)
async def add_version(
    case_id: int,
    body: VersionPayload,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
) -> VersionResponse:
    case = await get_case(session, actor.project_id, case_id, locked=True)
    version = await create_version(session, case, body, actor)
    await session.commit()
    await session.refresh(version, attribute_names=["tags"])
    return version_response(version)


@router.get("/{case_id}/versions", response_model=list[VersionResponse])
async def list_versions(
    case_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> list[VersionResponse]:
    case = await get_case(session, actor.project_id, case_id)
    return [
        version_response(item)
        for item in sorted(case.versions, key=lambda value: value.version_no, reverse=True)
    ]


@versions_router.get("/{version_id}", response_model=VersionResponse)
async def read_version(
    version_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> VersionResponse:
    return version_response(await get_version(session, actor.project_id, version_id))


@versions_router.patch("/{version_id}", response_model=VersionResponse)
async def patch_version(
    version_id: int,
    body: VersionPayload,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> VersionResponse:
    version = await get_version(session, actor.project_id, version_id, locked=True)
    await update_version(session, version, body, actor)
    await session.commit()
    return version_response(version)


@versions_router.post("/{version_id}/clone", response_model=VersionResponse, status_code=status.HTTP_201_CREATED)
async def clone_version(
    version_id: int,
    actor: Principal = Depends(require("testcase:create")),
    session: AsyncSession = Depends(get_session),
) -> VersionResponse:
    source = await get_version(session, actor.project_id, version_id)
    case = await get_case(session, actor.project_id, source.test_case_id, locked=True)
    payload = VersionPayload(
        map_code=source.map_code,
        ego_vehicle_code=source.ego_vehicle_code,
        adversary_type=source.adversary_type,
        environment_code=source.environment_code,
        danger_level=source.danger_level,
        scenario_input=source.scenario_input,
        tag_names=[tag_item.name for tag_item in source.tags],
        change_note=f"Cloned from v{source.version_no}",
    )
    version = await create_version(session, case, payload, actor)
    # The clone is meant for editing metadata/XOSC; keep the immutable artifact so it can be resubmitted as-is.
    version.xosc_artifact_id = source.xosc_artifact_id
    await session.commit()
    return version_response(version)


@versions_router.post("/{version_id}/xosc", response_model=UploadResponse)
async def post_xosc(
    version_id: int,
    file: UploadFile = File(...),
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    content = await file.read(get_settings().max_xosc_size_bytes + 1)
    if len(content) > get_settings().max_xosc_size_bytes:
        raise ValidationFailed("XOSC file exceeds configured size limit")
    version = await get_version(session, actor.project_id, version_id, locked=True)
    artifact = await upload_xosc(
        session,
        version,
        actor,
        content,
        file.filename or "scenario.xosc",
        file.content_type or "application/xml",
    )
    await session.commit()
    return UploadResponse(
        artifact_id=artifact.id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
    )


@versions_router.get("/{version_id}/xosc")
async def get_xosc(
    version_id: int,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    version = await get_version(session, actor.project_id, version_id)
    if version.xosc_artifact is None:
        raise ValidationFailed("Version has no XOSC artifact")
    return Response(
        version.xosc_artifact.content,
        media_type=version.xosc_artifact.content_type or "application/xml",
        headers={
            "Content-Disposition": content_disposition(
                version.xosc_artifact.original_name or "scenario.xosc"
            )
        },
    )
