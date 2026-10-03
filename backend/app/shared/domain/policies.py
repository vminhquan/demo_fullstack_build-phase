from app.shared.domain.errors import Conflict, Forbidden, ValidationFailed
from app.shared.infrastructure.models import ResponsibilityCode, RoleCode, VersionStatus

# Every project member, whatever role or responsibility, can read and run suites and see who holds
# which role/responsibility; only admins change them (member:manage).
BASE_PERMISSIONS: frozenset[str] = frozenset({"testcase:read", "suite:read", "suite:run", "member:read"})

ROLE_PERMISSIONS: dict[RoleCode, frozenset[str]] = {
    RoleCode.ADMIN: frozenset({"member:manage", "project:update", "audit:read"}),
    RoleCode.MEMBER: frozenset(),
}

# Granted only to the project creator, on top of their (always ADMIN) role.
OWNER_PERMISSIONS: frozenset[str] = frozenset({"project:delete"})

RESPONSIBILITY_PERMISSIONS: dict[ResponsibilityCode, frozenset[str]] = {
    ResponsibilityCode.TESTCASE_CREATE: frozenset(
        {"testcase:create", "testcase:update_own_draft", "testcase:submit_review", "suite:manage"}
    ),
    ResponsibilityCode.TESTCASE_REVIEW: frozenset({"review:read", "review:comment", "review:decide"}),
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


def ensure_can_decide(version_creator_id: int, actor_id: int, permissions: frozenset[str]) -> None:
    require_permission(permissions, "review:decide")
    if version_creator_id == actor_id and not has_permission(permissions, "review:decide_own"):
        raise Forbidden("You are not assigned to review your own test cases")


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


def ensure_editable(status: VersionStatus) -> None:
    if status is not VersionStatus.DRAFT:
        raise Conflict("Only DRAFT versions can be edited")


def ensure_suite_eligible(status: VersionStatus) -> None:
    if status is not VersionStatus.APPROVED:
        raise Conflict("Only APPROVED test case versions can be added to a suite")


def validate_reject_comment(comment: str | None) -> str:
    return validate_required_review_comment(comment, "A rejection comment is required")


def validate_required_review_comment(comment: str | None, message: str) -> str:
    value = (comment or "").strip()
    if not value:
        raise ValidationFailed(message)
    return value
