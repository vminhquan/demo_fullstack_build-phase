"""Explicit development bootstrap; normal API startup never creates accounts."""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.shared.infrastructure.db import SessionFactory
from app.shared.infrastructure.models import (
    OWNER_DEFAULT_RESPONSIBILITIES,
    AccountStatus,
    Project,
    ProjectUser,
    RoleCode,
    User,
)
from app.shared.infrastructure.security import hash_password


async def seed_admin() -> None:
    email = os.environ["INITIAL_ADMIN_EMAIL"].strip().lower()
    password = os.environ["INITIAL_ADMIN_PASSWORD"]
    display_name = os.environ.get("INITIAL_ADMIN_DISPLAY_NAME", "Scenario Forge Admin")
    project_name = os.environ.get("INITIAL_PROJECT_NAME", "Scenario Forge")
    project_code = os.environ.get("INITIAL_PROJECT_CODE", "DEFAULT")
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
                account_status=AccountStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
        project = await session.scalar(select(Project).where(Project.code == project_code))
        if project is None:
            project = Project(
                code=project_code,
                name=project_name,
                description="Default development project",
                created_by=user.id,
            )
            session.add(project)
            await session.flush()
        membership = await session.get(ProjectUser, (project.id, user.id))
        if membership is None:
            membership = ProjectUser(
                project_id=project.id,
                user_id=user.id,
                role=RoleCode.ADMIN,
                added_by=user.id,
            )
            membership.set_responsibilities(set(OWNER_DEFAULT_RESPONSIBILITIES), user.id)
            session.add(membership)
        await session.commit()
        print(f"Seeded admin {email} in project {project.code}")


if __name__ == "__main__":
    asyncio.run(seed_admin())
