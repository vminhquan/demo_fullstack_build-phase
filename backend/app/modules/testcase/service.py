"""Flat test cases (docs/20-kich-ban-khong-version.md): edited in place, decided, discarded, locked by runs."""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal
from app.modules.testcase.schemas import TestCaseContent, TestCaseResponse, TestCaseUpdate
from app.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.shared.domain.policies import (
    ensure_can_decide,
    ensure_editable,
    ensure_pending,
    ensure_unlocked,
    has_permission,
    is_own_case,
    require_permission,
)
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.models import (
    Artifact,
    ArtifactKind,
    EmbeddingStatus,
    Tag,
    TestCase,
    TestCaseDecision,
    TestCaseDecisionValue,
    TestCaseSearchDocument,
    TestCaseStatus,
    User,
)

EDITABLE_FIELDS = (
    "title", "description", "map_code", "ego_vehicle_code", "adversary_type",
    "environment_code", "danger_level", "scenario_input",
)


def config_sha256(case: TestCase) -> str:
    """Hash of everything a person can edit; 0007_flat_test_cases holds a frozen copy of this function."""
    payload = {
        "title": case.title,
        "description": case.description or "",
        "map_code": case.map_code,
        "ego_vehicle_code": case.ego_vehicle_code,
        "adversary_type": case.adversary_type,
        "environment_code": case.environment_code,
        "danger_level": getattr(case.danger_level, "value", case.danger_level),
        "scenario_input": case.scenario_input or {},
        "xosc_sha256": case.xosc_sha256 or "",
        "tags": sorted(tag.name for tag in case.tags),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def actions_for(case: TestCase, actor: Principal | None) -> list[str]:
    """Buttons the caller may use on this case right now (mirrors the checks in the service functions)."""
    if actor is None:
        return []
    permissions = actor.permissions
    actions = []
    if case.locked_at is None:
        if case.status is not TestCaseStatus.DISCARDED and has_permission(permissions, "testcase:edit"):
            actions.append("edit")
        if case.created_by == actor.id and has_permission(permissions, "testcase:discard"):
            if case.status is TestCaseStatus.PENDING:
                actions.append("discard")
            elif case.status is TestCaseStatus.DISCARDED:
                actions.append("restore")
    if case.status is TestCaseStatus.PENDING and has_permission(permissions, "review:decide"):
        own = is_own_case(case.created_by, case.last_edited_by, actor.id)
        if not own or has_permission(permissions, "review:decide_own"):
            actions.append("decide")
    return actions


async def user_names(session: AsyncSession, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.display_name, User.email).where(User.id.in_(wanted)))
    return {row.id: row.display_name or row.email for row in rows}


def case_response(case: TestCase, names: dict[int, str] | None = None, actor: Principal | None = None) -> TestCaseResponse:
    names = names or {}
    return TestCaseResponse(
        id=case.id,
        case_key=case.case_key,
        title=case.title,
        description=case.description,
        status=case.status,
        map_code=case.map_code,
        ego_vehicle_code=case.ego_vehicle_code,
        adversary_type=case.adversary_type,
        environment_code=case.environment_code,
        danger_level=case.danger_level,
        scenario_input=case.scenario_input or {},
        xosc_artifact_id=case.xosc_artifact_id,
        xosc_sha256=case.xosc_sha256,
        catalog_snapshot_id=case.catalog_snapshot_id,
        revision=case.revision,
        config_sha256=case.config_sha256,
        created_by=case.created_by,
        created_by_name=names.get(case.created_by),
        last_edited_by=case.last_edited_by,
        last_edited_by_name=names.get(case.last_edited_by) if case.last_edited_by else None,
        last_edited_at=case.last_edited_at,
        decided_by=case.decided_by,
        decided_by_name=names.get(case.decided_by) if case.decided_by else None,
        decided_at=case.decided_at,
        discarded_at=case.discarded_at,
        locked_at=case.locked_at,
        archived_at=case.archived_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        tags=sorted(tag.name for tag in case.tags),
        builder_session_id=case.builder_session_id,
        builder_variant_no=case.builder_variant_no,
        can=actions_for(case, actor),
    )


async def case_responses(session: AsyncSession, cases: list[TestCase], actor: Principal | None) -> list[TestCaseResponse]:
    names = await user_names(
        session, [item for case in cases for item in (case.created_by, case.last_edited_by, case.decided_by)]
    )
    return [case_response(case, names, actor) for case in cases]


async def get_case(session: AsyncSession, project_id: int, case_id: int, *, locked: bool = False) -> TestCase:
    statement = select(TestCase).where(TestCase.id == case_id, TestCase.project_id == project_id)
    if locked:
        statement = statement.with_for_update()
    case = await session.scalar(statement)
    if case is None:
        raise NotFound("Test case not found")
    return case


async def resolve_tags(session: AsyncSession, project_id: int, tag_names: Iterable[str]) -> list[Tag]:
    normalized = sorted({name.strip().lower() for name in tag_names if name.strip()})
    if any(len(name) > 120 for name in normalized):
        raise ValidationFailed("Tag name must be at most 120 characters")
    if not normalized:
        return []
    # ON CONFLICT: Test Case Builder saves variants in parallel and they often add the same new tag.
    await session.execute(
        pg_insert(Tag)
        .values([{"project_id": project_id, "name": name} for name in normalized])
        .on_conflict_do_nothing(constraint="uq_tag_project_name")
    )
    return list((await session.scalars(
        select(Tag).where(Tag.project_id == project_id, func.lower(Tag.name).in_(normalized))
    )).all())


async def index_keyword_document(session: AsyncSession, case: TestCase) -> None:
    text = "\n".join([
        f"Title: {case.title}",
        f"Description: {case.description or ''}",
        f"Map: {case.map_code}",
        f"Ego vehicle: {case.ego_vehicle_code}",
        f"Adversary: {case.adversary_type}",
        f"Environment: {case.environment_code}",
        f"Danger level: {getattr(case.danger_level, 'value', case.danger_level)}",
        f"Tags: {', '.join(sorted(tag.name for tag in case.tags))}",
    ])
    document = await session.scalar(
        select(TestCaseSearchDocument).where(
            TestCaseSearchDocument.project_id == case.project_id,
            TestCaseSearchDocument.test_case_id == case.id,
        )
    )
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if document is None:
        session.add(TestCaseSearchDocument(
            project_id=case.project_id,
            test_case_id=case.id,
            search_text=text,
            content_hash=content_hash,
            embedding_status=EmbeddingStatus.PENDING,
        ))
    elif document.content_hash != content_hash:
        document.search_text = text
        document.content_hash = content_hash
        document.embedding_status = EmbeddingStatus.PENDING


def store_xosc(session: AsyncSession, actor: Principal, content: bytes, filename: str, content_type: str) -> Artifact:
    if not filename.lower().endswith(".xosc"):
        raise ValidationFailed("Only .xosc files are accepted")
    if not content:
        raise ValidationFailed("XOSC file is empty")
    artifact = Artifact(
        project_id=actor.project_id,
        kind=ArtifactKind.XOSC,
        original_name=filename,
        content_type=content_type or "application/xml",
        content=content,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        created_by=actor.id,
    )
    session.add(artifact)
    return artifact


async def create_test_case(
    session: AsyncSession,
    actor: Principal,
    content: TestCaseContent,
    *,
    xosc: tuple[bytes, str] | None = None,
    catalog_snapshot_id: int | None = None,
    builder_session_id: int | None = None,
    builder_variant_no: int | None = None,
) -> TestCase:
    """New case straight into PENDING (no draft, no submit step)."""
    require_permission(actor.permissions, "testcase:create")
    case = TestCase(
        project_id=actor.project_id,
        case_key=f"TEMP-{secrets.token_hex(8).upper()}",
        title=content.title,
        description=content.description,
        status=TestCaseStatus.PENDING,
        map_code=content.map_code,
        ego_vehicle_code=content.ego_vehicle_code,
        adversary_type=content.adversary_type,
        environment_code=content.environment_code,
        danger_level=content.danger_level,
        scenario_input=content.scenario_input,
        catalog_snapshot_id=catalog_snapshot_id,
        revision=1,
        created_by=actor.id,
        builder_session_id=builder_session_id,
        builder_variant_no=builder_variant_no,
    )
    case.tags = await resolve_tags(session, actor.project_id, content.tag_names)
    if xosc is not None:
        artifact = store_xosc(session, actor, xosc[0], xosc[1], "application/xml")
        await session.flush()
        case.xosc_artifact_id = artifact.id
        case.xosc_sha256 = artifact.sha256
    session.add(case)
    await session.flush()
    case.case_key = f"TC-{case.id:06d}"
    case.config_sha256 = config_sha256(case)
    await index_keyword_document(session, case)
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_CREATED",
        entity_type="TEST_CASE",
        entity_id=case.id,
        after_data={"status": case.status.value, "map_code": case.map_code, "danger_level": case.danger_level.value},
    )
    return case


def _snapshot(case: TestCase) -> dict[str, Any]:
    data = {field: getattr(case, field) for field in EDITABLE_FIELDS}
    data["danger_level"] = getattr(case.danger_level, "value", case.danger_level)
    data["tags"] = sorted(tag.name for tag in case.tags)
    data["xosc_sha256"] = case.xosc_sha256
    return data


def _back_to_pending(case: TestCase, actor: Principal) -> None:
    case.status = TestCaseStatus.PENDING
    case.revision += 1
    case.last_edited_by = actor.id
    case.last_edited_at = datetime.now(UTC)
    # The previous decision covered the old content; its row stays in test_case_decisions.
    case.decided_by = None
    case.decided_at = None
    case.config_sha256 = config_sha256(case)


def _check_edit(case: TestCase, actor: Principal, expected_revision: int | None) -> None:
    require_permission(actor.permissions, "testcase:edit")
    ensure_editable(case.status, case.locked_at)
    if expected_revision is not None and expected_revision != case.revision:
        raise Conflict(
            "Test case was changed by someone else; reload before editing",
            {"code": "REVISION_CONFLICT", "current_revision": case.revision},
        )


async def edit_test_case(session: AsyncSession, case: TestCase, body: TestCaseUpdate, actor: Principal) -> bool:
    """Overwrite fields in place; returns False when nothing changed (status and revision untouched)."""
    _check_edit(case, actor, body.expected_revision)
    before = _snapshot(case)
    for field in EDITABLE_FIELDS:
        value = getattr(body, field)
        if value is not None:
            setattr(case, field, value)
    if body.tag_names is not None:
        case.tags = await resolve_tags(session, actor.project_id, body.tag_names)
    after = _snapshot(case)
    if after == before:
        return False
    previous_status = case.status
    _back_to_pending(case, actor)
    await index_keyword_document(session, case)
    changed = {key: value for key, value in after.items() if before.get(key) != value}
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_EDITED",
        entity_type="TEST_CASE",
        entity_id=case.id,
        before_data={**{key: before[key] for key in changed}, "status": previous_status.value},
        after_data={**changed, "status": case.status.value, "revision": case.revision},
    )
    return True


async def replace_xosc(
    session: AsyncSession, case: TestCase, actor: Principal, content: bytes, filename: str, content_type: str,
    expected_revision: int | None,
) -> Artifact:
    _check_edit(case, actor, expected_revision)
    artifact = store_xosc(session, actor, content, filename, content_type)
    await session.flush()
    previous = (case.status, case.xosc_sha256)
    case.xosc_artifact_id = artifact.id
    case.xosc_sha256 = artifact.sha256
    _back_to_pending(case, actor)
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_XOSC_REPLACED",
        entity_type="TEST_CASE",
        entity_id=case.id,
        before_data={"status": previous[0].value, "xosc_sha256": previous[1]},
        after_data={"status": case.status.value, "xosc_sha256": artifact.sha256, "revision": case.revision},
    )
    return artifact


async def decide_test_case(
    session: AsyncSession, case: TestCase, actor: Principal, decision: TestCaseDecisionValue,
    expected_revision: int | None = None,
) -> TestCaseDecision:
    ensure_pending(case.status)
    ensure_can_decide(case.created_by, case.last_edited_by, actor.id, actor.permissions)
    if expected_revision is not None and expected_revision != case.revision:
        raise Conflict(
            "Test case was edited after you opened it; review the new content",
            {"code": "REVISION_CONFLICT", "current_revision": case.revision},
        )
    now = datetime.now(UTC)
    case.status = TestCaseStatus(decision.value)
    case.decided_by = actor.id
    case.decided_at = now
    row = TestCaseDecision(
        project_id=case.project_id,
        test_case_id=case.id,
        decision=decision,
        revision=case.revision,
        config_sha256=case.config_sha256,
        decided_by=actor.id,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action=f"TEST_CASE_{decision.value}",
        entity_type="TEST_CASE",
        entity_id=case.id,
        before_data={"status": TestCaseStatus.PENDING.value},
        after_data={"status": case.status.value, "revision": case.revision},
    )
    return row


async def undo_decision(session: AsyncSession, case: TestCase, actor: Principal) -> None:
    """Undo the caller's latest decision, as long as nothing happened to the case since."""
    latest = await session.scalar(
        select(TestCaseDecision)
        .where(TestCaseDecision.test_case_id == case.id, TestCaseDecision.undone_at.is_(None))
        .order_by(TestCaseDecision.created_at.desc(), TestCaseDecision.id.desc())
        .limit(1)
        .with_for_update()
    )
    if (
        latest is None
        or latest.decided_by != actor.id
        or case.status.value != latest.decision.value
        or case.revision != latest.revision
    ):
        raise Conflict("There is no decision of yours to undo on this test case")
    ensure_unlocked(case.locked_at)
    latest.undone_at = datetime.now(UTC)
    case.status = TestCaseStatus.PENDING
    case.decided_by = None
    case.decided_at = None
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="TEST_CASE_DECISION_UNDONE",
        entity_type="TEST_CASE",
        entity_id=case.id,
        before_data={"status": latest.decision.value},
        after_data={"status": case.status.value},
    )


def _ensure_creator_can_tidy(case: TestCase, actor: Principal) -> None:
    require_permission(actor.permissions, "testcase:discard")
    if case.created_by != actor.id:
        raise Forbidden("Only the creator of a test case can discard or restore it")
    ensure_unlocked(case.locked_at)


async def discard_test_case(session: AsyncSession, case: TestCase, actor: Principal) -> None:
    _ensure_creator_can_tidy(case, actor)
    if case.status is not TestCaseStatus.PENDING:
        raise Conflict("Only PENDING test cases can be discarded", {"code": "NOT_PENDING"})
    case.status = TestCaseStatus.DISCARDED
    case.discarded_at = datetime.now(UTC)
    await record_audit(
        session, project_id=actor.project_id, actor_user_id=actor.id, action="TEST_CASE_DISCARDED",
        entity_type="TEST_CASE", entity_id=case.id,
        before_data={"status": TestCaseStatus.PENDING.value}, after_data={"status": case.status.value},
    )


async def restore_test_case(session: AsyncSession, case: TestCase, actor: Principal) -> None:
    _ensure_creator_can_tidy(case, actor)
    if case.status is not TestCaseStatus.DISCARDED:
        raise Conflict("Only discarded test cases can be restored")
    case.status = TestCaseStatus.PENDING
    case.discarded_at = None
    await record_audit(
        session, project_id=actor.project_id, actor_user_id=actor.id, action="TEST_CASE_RESTORED",
        entity_type="TEST_CASE", entity_id=case.id,
        before_data={"status": TestCaseStatus.DISCARDED.value}, after_data={"status": case.status.value},
    )


async def lock_for_run(session: AsyncSession, project_id: int, ids: list[int], actor_id: int | None) -> tuple[list[TestCase], list[tuple[int, str]]]:
    """Lock APPROVED cases put into a simulator run; returns (runnable cases, skipped (id, reason))."""
    wanted = list(dict.fromkeys(ids))
    cases = {
        case.id: case
        for case in (
            await session.scalars(
                select(TestCase)
                .where(TestCase.project_id == project_id, TestCase.id.in_(wanted))
                .with_for_update()
            )
        ).all()
    }
    runnable: list[TestCase] = []
    skipped: list[tuple[int, str]] = []
    now = datetime.now(UTC)
    newly_locked = []
    for case_id in wanted:
        case = cases.get(case_id)
        if case is None:
            skipped.append((case_id, "NOT_FOUND"))
        elif case.status is not TestCaseStatus.APPROVED:
            skipped.append((case_id, "NOT_APPROVED"))
        else:
            if case.locked_at is None:
                case.locked_at = now
                newly_locked.append(case.id)
            runnable.append(case)
    if newly_locked:
        await record_audit(
            session, project_id=project_id, actor_user_id=actor_id, action="TEST_CASES_LOCKED",
            entity_type="TEST_CASE", entity_id=newly_locked[0] if len(newly_locked) == 1 else None,
            after_data={"test_case_ids": newly_locked},
        )
    return runnable, skipped
