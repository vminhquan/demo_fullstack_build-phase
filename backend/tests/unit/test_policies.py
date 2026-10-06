import pytest

from app.shared.domain.errors import Conflict, Forbidden, ValidationFailed
from app.shared.domain.policies import (
    ensure_can_change_role,
    ensure_can_decide,
    ensure_can_leave,
    ensure_can_remove_member,
    ensure_editable,
    ensure_pending,
    ensure_suite_eligible,
    ensure_unlocked,
    permissions_of,
    require_permission,
    validate_responsibilities,
)
from app.shared.infrastructure.models import (
    OWNER_DEFAULT_RESPONSIBILITIES,
    ResponsibilityCode,
    RoleCode,
    TestCaseStatus as CaseStatus,
)

CREATE = ResponsibilityCode.TESTCASE_CREATE
REVIEW = ResponsibilityCode.TESTCASE_REVIEW
SELF_REVIEW = ResponsibilityCode.TESTCASE_SELF_REVIEW
OWNER_ID, ADMIN_ID, MEMBER_ID = 1, 2, 3


@pytest.mark.parametrize("status", [CaseStatus.PENDING, CaseStatus.APPROVED, CaseStatus.REJECTED])
def test_unlocked_cases_are_editable_except_discarded(status: CaseStatus) -> None:
    ensure_editable(status, None)
    with pytest.raises(Conflict):
        ensure_editable(CaseStatus.DISCARDED, None)


def test_locked_case_never_changes() -> None:
    with pytest.raises(Conflict):
        ensure_editable(CaseStatus.APPROVED, "2026-10-06T00:00:00Z")
    with pytest.raises(Conflict):
        ensure_unlocked("2026-10-06T00:00:00Z")


def test_only_pending_can_be_decided() -> None:
    ensure_pending(CaseStatus.PENDING)
    for status in (CaseStatus.APPROVED, CaseStatus.REJECTED, CaseStatus.DISCARDED):
        with pytest.raises(Conflict):
            ensure_pending(status)


def test_only_approved_is_suite_eligible() -> None:
    ensure_suite_eligible(CaseStatus.APPROVED)
    with pytest.raises(Conflict):
        ensure_suite_eligible(CaseStatus.PENDING)


def test_creators_and_reviewers_can_edit_only_creators_can_discard() -> None:
    assert "testcase:edit" in permissions_of(RoleCode.MEMBER, frozenset({CREATE}))
    assert "testcase:edit" in permissions_of(RoleCode.MEMBER, frozenset({REVIEW}))
    assert "testcase:discard" in permissions_of(RoleCode.MEMBER, frozenset({CREATE}))
    assert "testcase:discard" not in permissions_of(RoleCode.MEMBER, frozenset({REVIEW}))
    assert "testcase:edit" not in permissions_of(RoleCode.ADMIN, frozenset())


@pytest.mark.parametrize("role", list(RoleCode))
def test_every_member_can_read_and_run_suites(role: RoleCode) -> None:
    granted = permissions_of(role, frozenset())
    assert {"testcase:read", "suite:read", "suite:run", "member:read"} <= granted


def test_member_without_responsibilities_cannot_create_review_or_manage() -> None:
    granted = permissions_of(RoleCode.MEMBER, frozenset())
    for permission in ("testcase:create", "review:decide", "suite:manage", "member:manage", "project:delete", "audit:read"):
        with pytest.raises(Forbidden):
            require_permission(granted, permission)


def test_admin_role_does_not_imply_responsibilities() -> None:
    granted = permissions_of(RoleCode.ADMIN, frozenset())
    assert {"member:manage", "project:update", "audit:read"} <= granted
    assert "testcase:create" not in granted
    assert "review:decide" not in granted


def test_responsibilities_grant_testcase_permissions() -> None:
    assert "testcase:create" in permissions_of(RoleCode.MEMBER, frozenset({CREATE}))
    assert "suite:manage" in permissions_of(RoleCode.MEMBER, frozenset({CREATE}))
    assert "review:decide" in permissions_of(RoleCode.MEMBER, frozenset({REVIEW}))
    assert "member:manage" not in permissions_of(RoleCode.MEMBER, frozenset({CREATE, REVIEW}))


def test_owner_defaults_to_every_responsibility() -> None:
    assert OWNER_DEFAULT_RESPONSIBILITIES == {CREATE, REVIEW, SELF_REVIEW}


def test_self_review_requires_review() -> None:
    validate_responsibilities(set())
    validate_responsibilities({REVIEW, SELF_REVIEW})
    with pytest.raises(ValidationFailed):
        validate_responsibilities({CREATE, SELF_REVIEW})


def test_reviewer_cannot_decide_own_test_case_without_self_review() -> None:
    reviewer = permissions_of(RoleCode.MEMBER, frozenset({CREATE, REVIEW}))
    ensure_can_decide(created_by=OWNER_ID, last_edited_by=None, actor_id=MEMBER_ID, permissions=reviewer)
    with pytest.raises(Forbidden):
        ensure_can_decide(created_by=MEMBER_ID, last_edited_by=None, actor_id=MEMBER_ID, permissions=reviewer)


def test_reviewer_cannot_approve_a_case_they_edited_without_self_review() -> None:
    reviewer = permissions_of(RoleCode.MEMBER, frozenset({REVIEW}))
    with pytest.raises(Forbidden):
        ensure_can_decide(created_by=OWNER_ID, last_edited_by=MEMBER_ID, actor_id=MEMBER_ID, permissions=reviewer)


def test_self_reviewer_can_decide_own_test_case() -> None:
    self_reviewer = permissions_of(RoleCode.MEMBER, frozenset({CREATE, REVIEW, SELF_REVIEW}))
    ensure_can_decide(created_by=MEMBER_ID, last_edited_by=MEMBER_ID, actor_id=MEMBER_ID, permissions=self_reviewer)


def test_creator_without_review_cannot_decide() -> None:
    with pytest.raises(Forbidden):
        ensure_can_decide(OWNER_ID, None, MEMBER_ID, permissions_of(RoleCode.MEMBER, frozenset({CREATE})))


def test_non_owner_admin_can_demote_self() -> None:
    ensure_can_change_role(target_id=ADMIN_ID, project_owner_id=OWNER_ID)


def test_nobody_can_change_owner_role() -> None:
    with pytest.raises(Forbidden):
        ensure_can_change_role(target_id=OWNER_ID, project_owner_id=OWNER_ID)


def test_admin_can_change_other_members_role() -> None:
    ensure_can_change_role(target_id=MEMBER_ID, project_owner_id=OWNER_ID)


def test_admin_cannot_remove_self_or_owner() -> None:
    with pytest.raises(Forbidden):
        ensure_can_remove_member(actor_id=ADMIN_ID, target_id=ADMIN_ID, project_owner_id=OWNER_ID)
    with pytest.raises(Forbidden):
        ensure_can_remove_member(actor_id=ADMIN_ID, target_id=OWNER_ID, project_owner_id=OWNER_ID)
    ensure_can_remove_member(actor_id=ADMIN_ID, target_id=MEMBER_ID, project_owner_id=OWNER_ID)


def test_everyone_but_owner_can_leave() -> None:
    ensure_can_leave(actor_id=ADMIN_ID, project_owner_id=OWNER_ID)
    ensure_can_leave(actor_id=MEMBER_ID, project_owner_id=OWNER_ID)
    with pytest.raises(Forbidden):
        ensure_can_leave(actor_id=OWNER_ID, project_owner_id=OWNER_ID)


def test_only_owner_can_delete_project() -> None:
    with pytest.raises(Forbidden):
        require_permission(permissions_of(RoleCode.ADMIN, frozenset({CREATE, REVIEW})), "project:delete")
    require_permission(permissions_of(RoleCode.ADMIN, frozenset(), is_owner=True), "project:delete")
