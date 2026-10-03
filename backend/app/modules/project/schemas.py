from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.shared.infrastructure.models import (
    AccountStatus,
    ProjectStatus,
    ResponsibilityCode,
    RoleCode,
)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    description: str | None = Field(default=None, max_length=4000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=250)
    description: str | None = Field(default=None, max_length=4000)


class ProjectResponse(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    status: ProjectStatus
    created_by: int
    role: RoleCode
    responsibilities: list[ResponsibilityCode]
    is_owner: bool
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class ProjectMemberCreate(BaseModel):
    email: EmailStr
    role: RoleCode
    responsibilities: list[ResponsibilityCode] = Field(default_factory=list)


class ProjectMemberRoleUpdate(BaseModel):
    role: RoleCode


class ProjectMemberResponsibilitiesUpdate(BaseModel):
    responsibilities: list[ResponsibilityCode]


class ProjectUserResponse(BaseModel):
    id: int
    email: EmailStr
    display_name: str | None
    account_status: AccountStatus
    role: RoleCode
    responsibilities: list[ResponsibilityCode]
    is_owner: bool
    created_at: datetime
