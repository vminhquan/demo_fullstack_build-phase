from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.schemas import UserResponse
from app.shared.domain.errors import Forbidden, NotFound, Unauthorized
from app.shared.domain.policies import permissions_of, require_permission
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    AccountStatus,
    Project,
    ProjectStatus,
    ProjectUser,
    ResponsibilityCode,
    RoleCode,
    User,
)
from app.shared.infrastructure.security import decode_access_token

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    email: str


@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    project_id: int
    role: RoleCode
    responsibilities: frozenset[ResponsibilityCode]
    is_project_owner: bool

    @property
    def permissions(self) -> frozenset[str]:
        return permissions_of(self.role, self.responsibilities, is_owner=self.is_project_owner)


async def _authenticated_user(
    credentials: HTTPAuthorizationCredentials | None,
    session: AsyncSession,
) -> tuple[AuthenticatedUser, dict]:
    if credentials is None:
        raise Unauthorized("Authentication is required")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise Unauthorized("Invalid or expired access token") from exc
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or user.account_status is not AccountStatus.ACTIVE:
        raise Unauthorized("User is inactive or unavailable")
    return AuthenticatedUser(id=user.id, email=user.email), payload


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    user, _ = await _authenticated_user(credentials, session)
    return user


def project_path_param(project_id: int = Path(ge=1, description="Project the request belongs to")) -> int:
    """Declared on every project-scoped router (/projects/{project_id}/…) so the id is validated and documented."""
    return project_id


async def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    user, payload = await _authenticated_user(credentials, session)
    # The project in the URL decides which project the request acts on (membership is checked below);
    # routes outside /projects/{project_id}/ fall back to the project selected in the token.
    try:
        project_id = int(request.path_params.get("project_id") or payload["project_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Forbidden("Select an active project before using this resource") from exc
    return await _project_principal(session, user, project_id)


async def principal_from_token(session: AsyncSession, token: str | None, project_id: int) -> Principal:
    """Same checks as `current_principal` for callers that cannot send headers (browser WebSockets)."""
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
    user, _ = await _authenticated_user(credentials, session)
    return await _project_principal(session, user, project_id)


async def _project_principal(session: AsyncSession, user: AuthenticatedUser, project_id: int) -> Principal:
    # Role and responsibilities are read on every request so admin changes apply immediately,
    # not when the access token expires.
    row = (
        await session.execute(
            select(ProjectUser, Project.created_by)
            .join(Project, Project.id == ProjectUser.project_id)
            .where(
                ProjectUser.project_id == project_id,
                ProjectUser.user_id == user.id,
                Project.status == ProjectStatus.ACTIVE,
                Project.deleted_at.is_(None),
            )
        )
    ).tuples().first()
    if row is None:
        raise Forbidden("Project access is inactive or unavailable")
    membership, owner_id = row
    return Principal(
        id=user.id,
        email=user.email,
        project_id=project_id,
        role=membership.role,
        responsibilities=membership.responsibilities,
        is_project_owner=owner_id == user.id,
    )


def require(permission: str):
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        require_permission(principal.permissions, permission)
        return principal

    return dependency


async def get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFound("User not found")
    return user


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        account_status=user.account_status,
    )


def membership_view(project: Project, membership: ProjectUser) -> dict:
    """Role, responsibilities and effective permissions of one member, for API responses."""
    responsibilities = membership.responsibilities
    is_owner = project.created_by == membership.user_id
    return {
        "role": membership.role,
        "responsibilities": sorted(responsibilities, key=lambda item: item.value),
        "is_owner": is_owner,
        "permissions": sorted(permissions_of(membership.role, responsibilities, is_owner=is_owner)),
    }
