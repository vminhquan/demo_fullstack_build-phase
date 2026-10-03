from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import Principal
from app.modules.testcase.schemas import TestCaseResponse, VersionPayload, VersionResponse
from app.shared.domain.errors import NotFound, ValidationFailed
from app.shared.domain.policies import ensure_editable, require_permission
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.models import (
    Artifact,
    ArtifactKind,
    EmbeddingStatus,
    Tag,
    TestCase,
    TestCaseSearchDocument,
    TestCaseVersion,
)


def version_response(version: TestCaseVersion | None) -> VersionResponse | None:
    if version is None:
        return None
    return VersionResponse(
        id=version.id,
        test_case_id=version.test_case_id,
        version_no=version.version_no,
        status=version.status,
        map_code=version.map_code,
        ego_vehicle_code=version.ego_vehicle_code,
        adversary_type=version.adversary_type,
        environment_code=version.environment_code,
        danger_level=version.danger_level,
        scenario_input=version.scenario_input,
        xosc_artifact_id=version.xosc_artifact_id,
        change_note=version.change_note,
        created_by=version.created_by,
        created_at=version.created_at,
        submitted_at=version.submitted_at,
        decided_at=version.decided_at,
        decided_by=version.decided_by,
        tags=sorted(tag.name for tag in version.tags),
    )


def case_response(case: TestCase) -> TestCaseResponse:
    latest = max(case.versions, key=lambda item: item.version_no) if case.versions else None
    return TestCaseResponse(
        id=case.id,
        case_key=case.case_key,
        title=case.title,
        description=case.description,
        created_by=case.created_by,
        created_at=case.created_at,
        updated_at=case.updated_at,
        archived_at=case.archived_at,
        latest_version=version_response(latest),
    )


async def get_case(
    session: AsyncSession,
    project_id: int,
    case_id: int,
    *,
    locked: bool = False,
) -> TestCase:
    statement = (
        select(TestCase)
        .where(TestCase.id == case_id, TestCase.project_id == project_id)
        .options(selectinload(TestCase.versions).selectinload(TestCaseVersion.tags))
    )
    if locked:
        statement = statement.with_for_update()
    case = await session.scalar(statement)
    if case is None:
        raise NotFound("Test case not found")
    return case


async def get_version(
    session: AsyncSession,
    project_id: int,
    version_id: int,
    *,
    locked: bool = False,
) -> TestCaseVersion:
    statement = (
        select(TestCaseVersion)
        .where(
            TestCaseVersion.id == version_id,
            TestCaseVersion.project_id == project_id,
        )
        .options(
            selectinload(TestCaseVersion.tags),
            selectinload(TestCaseVersion.xosc_artifact),
        )
    )
    if locked:
        statement = statement.with_for_update()
    version = await session.scalar(statement)
    if version is None:
        raise NotFound("Test case version not found")
    return version


async def resolve_tags(
    session: AsyncSession,
    project_id: int,
    tag_names: Iterable[str],
) -> list[Tag]:
    normalized = sorted({name.strip().lower() for name in tag_names if name.strip()})
    if any(len(name) > 120 for name in normalized):
        raise ValidationFailed("Tag name must be at most 120 characters")
    if not normalized:
        return []
    existing = {
        tag.name.lower(): tag
        for tag in (
            await session.scalars(
                select(Tag).where(
                    Tag.project_id == project_id,
                    func.lower(Tag.name).in_(normalized),
                )
            )
        ).all()
    }
    tags = list(existing.values())
    for name in normalized:
        if name not in existing:
            tag = Tag(project_id=project_id, name=name)
            session.add(tag)
            tags.append(tag)
    return tags


async def index_keyword_document(
    session: AsyncSession,
    case: TestCase,
    version: TestCaseVersion,
) -> None:
    text = "\n".join(
        [
            f"Title: {case.title}",
            f"Description: {case.description or ''}",
            f"Map: {version.map_code}",
            f"Ego vehicle: {version.ego_vehicle_code}",
            f"Adversary: {version.adversary_type}",
            f"Environment: {version.environment_code}",
            f"Danger level: {version.danger_level.value}",
            f"Tags: {', '.join(sorted(tag.name for tag in version.tags))}",
        ]
    )
    document = await session.scalar(
        select(TestCaseSearchDocument).where(
            TestCaseSearchDocument.project_id == case.project_id,
            TestCaseSearchDocument.version_id == version.id,
        )
    )
    if document is None:
        session.add(
            TestCaseSearchDocument(
                project_id=case.project_id,
                version_id=version.id,
                search_text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                embedding_status=EmbeddingStatus.PENDING,
            )
        )
    else:
        document.search_text = text
        document.content_hash = hashlib.sha256(text.encode()).hexdigest()
        document.embedding_status = EmbeddingStatus.PENDING


async def create_version(
    session: AsyncSession,
    case: TestCase,
    payload: VersionPayload,
    actor: Principal,
) -> TestCaseVersion:
    require_permission(actor.permissions, "testcase:create")
    next_number = max((item.version_no for item in case.versions), default=0) + 1
    version = TestCaseVersion(
        project_id=actor.project_id,
        test_case_id=case.id,
        version_no=next_number,
        created_by=actor.id,
        map_code=payload.map_code,
        ego_vehicle_code=payload.ego_vehicle_code,
        adversary_type=payload.adversary_type,
        environment_code=payload.environment_code,
        danger_level=payload.danger_level,
        scenario_input=payload.scenario_input,
        change_note=payload.change_note,
    )
    version.tags = await resolve_tags(session, actor.project_id, payload.tag_names)
    session.add(version)
    await session.flush()
    await index_keyword_document(session, case, version)
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="VERSION_CREATED",
        entity_type="TEST_CASE_VERSION",
        entity_id=version.id,
        entity_version_id=version.id,
        after_data={"version_no": next_number, "status": version.status.value},
    )
    return version


def ensure_draft_owner(version: TestCaseVersion, actor: Principal) -> None:
    ensure_editable(version.status)
    if version.created_by != actor.id:
        require_permission(actor.permissions, "member:manage")
    else:
        require_permission(actor.permissions, "testcase:update_own_draft")


async def update_version(
    session: AsyncSession,
    version: TestCaseVersion,
    payload: VersionPayload,
    actor: Principal,
) -> None:
    ensure_draft_owner(version, actor)
    before = {"status": version.status.value, "map_code": version.map_code}
    for field in (
        "map_code",
        "ego_vehicle_code",
        "adversary_type",
        "environment_code",
        "danger_level",
        "scenario_input",
        "change_note",
    ):
        setattr(version, field, getattr(payload, field))
    version.tags = await resolve_tags(session, actor.project_id, payload.tag_names)
    case = await get_case(session, actor.project_id, version.test_case_id)
    await index_keyword_document(session, case, version)
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="VERSION_UPDATED",
        entity_type="TEST_CASE_VERSION",
        entity_id=version.id,
        entity_version_id=version.id,
        before_data=before,
        after_data={"map_code": version.map_code},
    )


async def upload_xosc(
    session: AsyncSession,
    version: TestCaseVersion,
    actor: Principal,
    content: bytes,
    filename: str,
    content_type: str,
) -> Artifact:
    ensure_draft_owner(version, actor)
    if not filename.lower().endswith(".xosc"):
        raise ValidationFailed("Only .xosc files are accepted")
    if not content:
        raise ValidationFailed("XOSC file is empty")
    digest = hashlib.sha256(content).hexdigest()
    artifact = Artifact(
        project_id=actor.project_id,
        kind=ArtifactKind.XOSC,
        original_name=filename,
        content_type=content_type or "application/xml",
        content=content,
        size_bytes=len(content),
        sha256=digest,
        created_by=actor.id,
    )
    session.add(artifact)
    await session.flush()
    version.xosc_artifact_id = artifact.id
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="VERSION_XOSC_UPDATED",
        entity_type="TEST_CASE_VERSION",
        entity_id=version.id,
        entity_version_id=version.id,
        after_data={"artifact_id": artifact.id, "sha256": digest},
    )
    return artifact
