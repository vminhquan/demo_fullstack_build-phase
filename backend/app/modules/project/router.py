from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import (
    AuthenticatedUser,
    Principal,
    current_user,
    membership_view,
    require,
)
from app.modules.project.schemas import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberResponsibilitiesUpdate,
    ProjectMemberRoleUpdate,
    ProjectResponse,
    ProjectUpdate,
    ProjectUserResponse,
)
from app.shared.domain.errors import Conflict, NotFound
from app.shared.domain.policies import (
    ensure_can_change_role,
    ensure_can_leave,
    ensure_can_remove_member,
    permissions_of,
    require_permission,
    validate_responsibilities,
)
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    OWNER_DEFAULT_RESPONSIBILITIES,
    AccountStatus,
    Project,
    ProjectUser,
    RoleCode,
    User,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def project_response(project: Project, membership: ProjectUser) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        code=project.code,
        name=project.name,
        description=project.description,
        status=project.status,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
        **membership_view(project, membership),
    )


def member_response(link: ProjectUser, project: Project) -> ProjectUserResponse:
    return ProjectUserResponse(
        id=link.user.id,
        email=link.user.email,
        display_name=link.user.display_name,
        account_status=link.user.account_status,
        role=link.role,
        responsibilities=sorted(link.responsibilities, key=lambda item: item.value),
        is_owner=link.user_id == project.created_by,
        created_at=link.created_at,
    )


def responsibility_values(link: ProjectUser) -> list[str]:
    return sorted(item.value for item in link.responsibilities)


async def get_project_for_user(
    session: AsyncSession,
    project_id: int,
    user_id: int,
    *,
    locked: bool = False,
) -> tuple[Project, ProjectUser]:
    statement = (
        select(Project, ProjectUser)
        .join(ProjectUser, ProjectUser.project_id == Project.id)
        .where(
            Project.id == project_id,
            Project.deleted_at.is_(None),
            ProjectUser.user_id == user_id,
        )
    )
    if locked:
        statement = statement.with_for_update(of=Project)
    row = (await session.execute(statement)).tuples().first()
    if row is None:
        raise NotFound("Project not found")
    return row[0], row[1]


async def get_member(
    session: AsyncSession,
    project_id: int,
    user_id: int,
) -> ProjectUser:
    link = await session.scalar(
        select(ProjectUser)
        .where(ProjectUser.project_id == project_id, ProjectUser.user_id == user_id)
        .options(selectinload(ProjectUser.user))
        .with_for_update(of=ProjectUser)
    )
    if link is None:
        raise NotFound("Project user not found")
    return link


def require_project_permission(project: Project, membership: ProjectUser, permission: str) -> None:
    """Checks the caller's permission in this project, which need not be their active one."""
    require_permission(
        permissions_of(
            membership.role,
            membership.responsibilities,
            is_owner=project.created_by == membership.user_id,
        ),
        permission,
    )


def ensure_current_project(project_id: int, actor: Principal) -> None:
    if project_id != actor.project_id:
        raise NotFound("Project not found")


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = Project(
        code=f"PRJ-{secrets.token_hex(4).upper()}",
        name=body.name,
        description=body.description,
        created_by=actor.id,
    )
    session.add(project)
    await session.flush()
    membership = ProjectUser(
        project_id=project.id,
        user_id=actor.id,
        role=RoleCode.ADMIN,
        added_by=actor.id,
    )
    membership.set_responsibilities(set(OWNER_DEFAULT_RESPONSIBILITIES), actor.id)
    session.add(membership)
    await record_audit(
        session,
        project_id=project.id,
        actor_user_id=actor.id,
        action="PROJECT_CREATED",
        entity_type="PROJECT",
        entity_id=project.id,
        after_data={"role": RoleCode.ADMIN.value, "responsibilities": responsibility_values(membership)},
    )
    await session.commit()
    return project_response(project, membership)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResponse]:
    rows = (
        await session.execute(
            select(Project, ProjectUser)
            .join(ProjectUser, ProjectUser.project_id == Project.id)
            .where(ProjectUser.user_id == actor.id, Project.deleted_at.is_(None))
            .order_by(Project.name)
        )
    ).tuples().all()
    return [project_response(project, membership) for project, membership in rows]


@router.get("/{project_id}", response_model=ProjectResponse)
async def read_project(
    project_id: int,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project, membership = await get_project_for_user(session, project_id, actor.id)
    return project_response(project, membership)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    # Not tied to the active project: the project list offers edit on every card.
    project, membership = await get_project_for_user(session, project_id, actor.id, locked=True)
    require_project_permission(project, membership, "project:update")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await record_audit(
        session,
        project_id=project.id,
        actor_user_id=actor.id,
        action="PROJECT_UPDATED",
        entity_type="PROJECT",
        entity_id=project.id,
    )
    await session.commit()
    return project_response(project, membership)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    project, membership = await get_project_for_user(session, project_id, actor.id, locked=True)
    require_project_permission(project, membership, "project:delete")
    project.deleted_at = datetime.now(UTC)
    project.deleted_by = actor.id
    await record_audit(
        session,
        project_id=project.id,
        actor_user_id=actor.id,
        action="PROJECT_DELETED",
        entity_type="PROJECT",
        entity_id=project.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_project(
    project_id: int,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Not tied to the active project: a user can leave any project from the project list.
    project, membership = await get_project_for_user(session, project_id, actor.id, locked=True)
    ensure_can_leave(actor.id, project.created_by)
    await record_audit(
        session,
        project_id=project.id,
        actor_user_id=actor.id,
        action="PROJECT_USER_LEFT",
        entity_type="USER",
        entity_id=actor.id,
        before_data={"role": membership.role.value, "responsibilities": responsibility_values(membership)},
    )
    await session.delete(membership)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/users", response_model=list[ProjectUserResponse])
async def list_project_users(
    project_id: int,
    actor: Principal = Depends(require("member:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectUserResponse]:
    ensure_current_project(project_id, actor)
    project, _ = await get_project_for_user(session, project_id, actor.id)
    links = (
        await session.scalars(
            select(ProjectUser)
            .where(ProjectUser.project_id == project_id)
            .options(selectinload(ProjectUser.user))
            .order_by(ProjectUser.created_at)
        )
    ).all()
    return [member_response(link, project) for link in links]


@router.post(
    "/{project_id}/users",
    response_model=ProjectUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_user(
    project_id: int,
    body: ProjectMemberCreate,
    actor: Principal = Depends(require("member:manage")),
    session: AsyncSession = Depends(get_session),
) -> ProjectUserResponse:
    ensure_current_project(project_id, actor)
    responsibilities = set(body.responsibilities)
    validate_responsibilities(responsibilities)
    project, _ = await get_project_for_user(session, project_id, actor.id)
    email = body.email.lower()
    user = await session.scalar(select(User).where(User.email == email).with_for_update())
    if user is None:
        user = User(
            email=email,
            password_hash=None,
            display_name=None,
            account_status=AccountStatus.PENDING_REGISTRATION,
        )
        session.add(user)
        await session.flush()
    if await session.get(ProjectUser, (project_id, user.id)) is not None:
        raise Conflict("User is already in this project")
    link = ProjectUser(
        project_id=project_id,
        user_id=user.id,
        role=body.role,
        added_by=actor.id,
        user=user,
    )
    link.set_responsibilities(responsibilities, actor.id)
    session.add(link)
    await record_audit(
        session,
        project_id=project_id,
        actor_user_id=actor.id,
        action="PROJECT_USER_ADDED",
        entity_type="USER",
        entity_id=user.id,
        after_data={
            "email": email,
            "role": body.role.value,
            "responsibilities": responsibility_values(link),
        },
    )
    await session.commit()
    return member_response(link, project)


@router.patch("/{project_id}/users/{user_id}/role", response_model=ProjectUserResponse)
async def update_project_user_role(
    project_id: int,
    user_id: int,
    body: ProjectMemberRoleUpdate,
    actor: Principal = Depends(require("member:manage")),
    session: AsyncSession = Depends(get_session),
) -> ProjectUserResponse:
    ensure_current_project(project_id, actor)
    project, _ = await get_project_for_user(session, project_id, actor.id)
    link = await get_member(session, project_id, user_id)
    ensure_can_change_role(user_id, project.created_by)
    before = link.role
    link.role = body.role
    await record_audit(
        session,
        project_id=project_id,
        actor_user_id=actor.id,
        action="PROJECT_USER_ROLE_CHANGED",
        entity_type="USER",
        entity_id=user_id,
        before_data={"role": before.value},
        after_data={"role": link.role.value},
    )
    await session.commit()
    return member_response(link, project)


@router.put(
    "/{project_id}/users/{user_id}/responsibilities",
    response_model=ProjectUserResponse,
)
async def update_project_user_responsibilities(
    project_id: int,
    user_id: int,
    body: ProjectMemberResponsibilitiesUpdate,
    actor: Principal = Depends(require("member:manage")),
    session: AsyncSession = Depends(get_session),
) -> ProjectUserResponse:
    ensure_current_project(project_id, actor)
    responsibilities = set(body.responsibilities)
    validate_responsibilities(responsibilities)
    project, _ = await get_project_for_user(session, project_id, actor.id)
    link = await get_member(session, project_id, user_id)
    before = responsibility_values(link)
    link.set_responsibilities(responsibilities, actor.id)
    await record_audit(
        session,
        project_id=project_id,
        actor_user_id=actor.id,
        action="PROJECT_USER_RESPONSIBILITIES_CHANGED",
        entity_type="USER",
        entity_id=user_id,
        before_data={"responsibilities": before},
        after_data={"responsibilities": responsibility_values(link)},
    )
    await session.commit()
    return member_response(link, project)


@router.delete("/{project_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_user(
    project_id: int,
    user_id: int,
    actor: Principal = Depends(require("member:manage")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ensure_current_project(project_id, actor)
    project, _ = await get_project_for_user(session, project_id, actor.id)
    link = await get_member(session, project_id, user_id)
    ensure_can_remove_member(actor.id, user_id, project.created_by)
    await record_audit(
        session,
        project_id=project_id,
        actor_user_id=actor.id,
        action="PROJECT_USER_REMOVED",
        entity_type="USER",
        entity_id=user_id,
        before_data={"role": link.role.value, "responsibilities": responsibility_values(link)},
    )
    await session.delete(link)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
