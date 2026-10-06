from app.shared.domain.errors import Conflict, Forbidden, ValidationFailed
from app.shared.infrastructure.models import ResponsibilityCode, RoleCode, TestCaseStatus

# Every project member, whatever role or responsibility, can read and run suites and see who holds
# which role/responsibility; only admins change them (member:manage).
BASE_PERMISSIONS: frozenset[str] = frozenset({"testcase:read", "suite:read", "suite:run", "member:read"})

ROLE_PERMISSIONS: dict[RoleCode, frozenset[str]] = {
    RoleCode.ADMIN: frozenset({"member:manage", "project:update", "audit:read", "catalog:import", "odd:manage"}),
    RoleCode.MEMBER: frozenset(),
}

# Granted only to the project creator, on top of their (always ADMIN) role.
OWNER_PERMISSIONS: frozenset[str] = frozenset({"project:delete"})

RESPONSIBILITY_PERMISSIONS: dict[ResponsibilityCode, frozenset[str]] = {
    ResponsibilityCode.TESTCASE_CREATE: frozenset(
        {"testcase:create", "testcase:edit", "testcase:discard", "suite:manage", "catalog:import", "odd:manage"}
    ),
    # Reviewers may make small in-place edits too; the edit sends the case back to PENDING.
    ResponsibilityCode.TESTCASE_REVIEW: frozenset({"testcase:edit", "review:decide"}),
    ResponsibilityCode.TESTCASE_SELF_REVIEW: frozenset({"review:decide_own"}),
}


def permissions_of(
    role: RoleCode,
    responsibilities: frozenset[ResponsibilityCode],
    *,
    is_owner: bool = False,
) -> frozenset[str]:
    granted = set(BASE_PERMISSIONS | ROLE_PERMISSIONS[role])
    if is_owner:
        granted |= OWNER_PERMISSIONS
    for item in responsibilities:
        granted |= RESPONSIBILITY_PERMISSIONS[item]
    return frozenset(granted)


def has_permission(permissions: frozenset[str], permission: str) -> bool:
    return permission in permissions


def require_permission(permissions: frozenset[str], permission: str) -> None:
    if not has_permission(permissions, permission):
        raise Forbidden(f"Permission '{permission}' is required")


def validate_responsibilities(items: set[ResponsibilityCode]) -> None:
    if (
        ResponsibilityCode.TESTCASE_SELF_REVIEW in items
        and ResponsibilityCode.TESTCASE_REVIEW not in items
    ):
        raise ValidationFailed("TESTCASE_SELF_REVIEW requires TESTCASE_REVIEW")


def is_own_case(created_by: int, last_edited_by: int | None, actor_id: int) -> bool:
    """A case counts as the actor's own if they created it or made its latest edit (no edit-then-self-approve)."""
    return actor_id in (created_by, last_edited_by)


def ensure_can_decide(created_by: int, last_edited_by: int | None, actor_id: int, permissions: frozenset[str]) -> None:
    require_permission(permissions, "review:decide")
    if is_own_case(created_by, last_edited_by, actor_id) and not has_permission(permissions, "review:decide_own"):
        raise Forbidden("Bạn không có quyền tự duyệt test case do chính mình tạo hoặc sửa")


def ensure_pending(status: TestCaseStatus) -> None:
    if status is not TestCaseStatus.PENDING:
        raise Conflict("Only PENDING test cases can be decided", {"code": "NOT_PENDING"})


def ensure_unlocked(locked_at: object | None) -> None:
    if locked_at is not None:
        raise Conflict("Test case was put into a simulator run and can no longer change", {"code": "TEST_CASE_LOCKED"})


def ensure_editable(status: TestCaseStatus, locked_at: object | None) -> None:
    ensure_unlocked(locked_at)
    if status is TestCaseStatus.DISCARDED:
        raise Conflict("Restore a discarded test case before editing it")


def ensure_can_change_role(target_id: int, project_owner_id: int) -> None:
    # Any admin may demote themselves; only the creator's role is fixed.
    if target_id == project_owner_id:
        raise Forbidden("The project creator's role cannot be changed")


def ensure_can_remove_member(actor_id: int, target_id: int, project_owner_id: int) -> None:
    if target_id == actor_id:
        raise Forbidden("You cannot remove yourself; leave the project instead")
    if target_id == project_owner_id:
        raise Forbidden("The project creator cannot be removed")


def ensure_can_leave(actor_id: int, project_owner_id: int) -> None:
    if actor_id == project_owner_id:
        raise Forbidden("The project creator cannot leave the project")


def ensure_suite_eligible(status: TestCaseStatus) -> None:
    if status is not TestCaseStatus.APPROVED:
        raise Conflict("Only APPROVED test cases can be added to a suite")
