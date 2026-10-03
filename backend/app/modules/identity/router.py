from __future__ import annotations

from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import (
    AuthenticatedUser,
    bearer,
    current_user,
    get_user_or_404,
    membership_view,
    to_user_response,
)
from app.modules.identity.schemas import (
    CurrentSessionResponse,
    LoginRequest,
    ProjectSummary,
    RefreshRequest,
    RegisterRequest,
    SelectProjectRequest,
    TokenResponse,
)
from app.shared.domain.errors import Conflict, Forbidden, Unauthorized
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    AccountStatus,
    AuthSession,
    Project,
    ProjectStatus,
    ProjectUser,
    User,
)
from app.shared.infrastructure.security import (
    decode_access_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    refresh_expiry,
    verify_password,
)

router = APIRouter(tags=["auth"])


async def project_summaries(session: AsyncSession, user_id: int) -> list[ProjectSummary]:
    rows = (
        await session.execute(
            select(Project, ProjectUser)
            .join(ProjectUser, ProjectUser.project_id == Project.id)
            .where(ProjectUser.user_id == user_id, Project.deleted_at.is_(None))
            .order_by(Project.name)
        )
    ).tuples().all()
    return [
        ProjectSummary(
            id=project.id,
            code=project.code,
            name=project.name,
            status=project.status,
            **membership_view(project, membership),
        )
        for project, membership in rows
    ]



async def issue_session(
    session: AsyncSession,
    user: User,
    project_id: int | None = None,
) -> TokenResponse:
    projects = await project_summaries(session, user.id)
    active = next((item for item in projects if item.id == project_id), None)
    if project_id is not None and (active is None or active.status is not ProjectStatus.ACTIVE):
        raise Forbidden("Project access is inactive or unavailable")
    if project_id is None:
        active = next((item for item in projects if item.status is ProjectStatus.ACTIVE), None)
    raw_refresh = new_refresh_token()
    session.add(
        AuthSession(
            user_id=user.id,
            project_id=active.id if active else None,
            refresh_token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_expiry(),
        )
    )
    return TokenResponse(
        access_token=issue_access_token(
            user.id,
            active.id if active else None,
            active.role.value if active else None,
        ),
        refresh_token=raw_refresh,
        user=to_user_response(user),
        projects=projects,
        active_project=active,
    )


def active_project_from_token(credentials: HTTPAuthorizationCredentials | None) -> int | None:
    if credentials is None:
        return None
    try:
        value = decode_access_token(credentials.credentials).get("project_id")
        return int(value) if value is not None else None
    except (jwt.InvalidTokenError, TypeError, ValueError):
        return None


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = body.email.lower()
    user = await session.scalar(select(User).where(User.email == email).with_for_update())
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(body.password),
            display_name=body.display_name,
            account_status=AccountStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
    elif user.account_status is AccountStatus.PENDING_REGISTRATION:
        user.password_hash = hash_password(body.password)
        user.display_name = body.display_name
        user.account_status = AccountStatus.ACTIVE
    else:
        raise Conflict("Email is already registered")
    token = await issue_session(session, user)
    await record_audit(
        session,
        project_id=token.active_project.id if token.active_project else None,
        actor_user_id=user.id,
        action="USER_REGISTERED",
        entity_type="USER",
        entity_id=user.id,
    )
    await session.commit()
    return token


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = await session.scalar(
        select(User)
        .where(User.email == body.email.lower())
        .options(selectinload(User.project_links))
    )
    if (
        user is None
        or user.account_status is not AccountStatus.ACTIVE
        or user.password_hash is None
        or not verify_password(body.password, user.password_hash)
    ):
        raise Unauthorized("Invalid email or password")
    # Signing in changes no project data, so it is not written to the audit log.
    token = await issue_session(session, user)
    await session.commit()
    return token


@router.post("/auth/select-project", response_model=TokenResponse)
async def select_project(
    body: SelectProjectRequest,
    authenticated: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    user = await get_user_or_404(session, authenticated.id)
    token = await issue_session(session, user, body.project_id)
    if body.refresh_token:
        previous = await session.scalar(
            select(AuthSession)
            .where(
                AuthSession.refresh_token_hash == hash_refresh_token(body.refresh_token),
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if previous is not None:
            previous.revoked_at = datetime.now(UTC)
    # Opening a project changes nothing, so it is not written to the audit log.
    await session.commit()
    return token


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    token_hash = hash_refresh_token(body.refresh_token)
    auth_session = await session.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == token_hash)
        .with_for_update()
    )
    if (
        auth_session is None
        or auth_session.revoked_at
        or auth_session.expires_at < datetime.now(UTC)
    ):
        raise Unauthorized("Refresh token is invalid or expired")
    user = await get_user_or_404(session, auth_session.user_id)
    if user.account_status is not AccountStatus.ACTIVE:
        raise Unauthorized("User is inactive or unavailable")
    auth_session.revoked_at = datetime.now(UTC)
    auth_session.last_used_at = datetime.now(UTC)
    try:
        token = await issue_session(session, user, auth_session.project_id)
    except Forbidden:
        # The session's project was archived or the membership revoked: keep the user signed in and
        # fall back to their first active project instead of failing every future refresh.
        token = await issue_session(session, user, None)
    await session.commit()
    return token


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> Response:
    auth_session = await session.scalar(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == hash_refresh_token(body.refresh_token)
        )
    )
    if auth_session and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=CurrentSessionResponse)
async def me(
    authenticated: AuthenticatedUser = Depends(current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentSessionResponse:
    user = await get_user_or_404(session, authenticated.id)
    projects = await project_summaries(session, user.id)
    active_id = active_project_from_token(credentials)
    return CurrentSessionResponse(
        user=to_user_response(user),
        projects=projects,
        active_project=next((item for item in projects if item.id == active_id), None),
    )


@router.post("/auth/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all(
    authenticated: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == authenticated.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
