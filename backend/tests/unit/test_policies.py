import pytest

from app.shared.domain.errors import Conflict, Forbidden, ValidationFailed
from app.shared.domain.policies import (
    ensure_can_change_role,
    ensure_can_decide,
    ensure_can_leave,
    ensure_can_remove_member,
    ensure_editable,
    ensure_suite_eligible,
    permissions_of,
    require_permission,
    validate_reject_comment,
    validate_required_review_comment,
    validate_responsibilities,
)
from app.shared.infrastructure.models import (
    OWNER_DEFAULT_RESPONSIBILITIES,
    ResponsibilityCode,
    RoleCode,
    VersionStatus,
)

CREATE = ResponsibilityCode.TESTCASE_CREATE
REVIEW = ResponsibilityCode.TESTCASE_REVIEW
SELF_REVIEW = ResponsibilityCode.TESTCASE_SELF_REVIEW
OWNER_ID, ADMIN_ID, MEMBER_ID = 1, 2, 3


def test_only_draft_is_editable() -> None:
    ensure_editable(VersionStatus.DRAFT)
    with pytest.raises(Conflict):
        ensure_editable(VersionStatus.IN_REVIEW)


def test_only_approved_is_suite_eligible() -> None:
    ensure_suite_eligible(VersionStatus.APPROVED)
    with pytest.raises(Conflict):
        ensure_suite_eligible(VersionStatus.REJECTED)


def test_reject_requires_comment() -> None:
    with pytest.raises(ValidationFailed):
        validate_reject_comment("  ")
    assert validate_reject_comment("Need a clearer trigger") == "Need a clearer trigger"


def test_edit_request_requires_comment() -> None:
    with pytest.raises(ValidationFailed):
        validate_required_review_comment("  ", "An edit request comment is required")
    assert validate_required_review_comment("Move the pedestrian spawn point", "ignored") == "Move the pedestrian spawn point"


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
    ensure_can_decide(version_creator_id=OWNER_ID, actor_id=MEMBER_ID, permissions=reviewer)
    with pytest.raises(Forbidden):
        ensure_can_decide(version_creator_id=MEMBER_ID, actor_id=MEMBER_ID, permissions=reviewer)


def test_self_reviewer_can_decide_own_test_case() -> None:
    self_reviewer = permissions_of(RoleCode.MEMBER, frozenset({CREATE, REVIEW, SELF_REVIEW}))
    ensure_can_decide(version_creator_id=MEMBER_ID, actor_id=MEMBER_ID, permissions=self_reviewer)


def test_creator_without_review_cannot_decide() -> None:
    with pytest.raises(Forbidden):
        ensure_can_decide(OWNER_ID, MEMBER_ID, permissions_of(RoleCode.MEMBER, frozenset({CREATE})))


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
