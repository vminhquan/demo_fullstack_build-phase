from pydantic import BaseModel, EmailStr, Field

from app.shared.infrastructure.models import (
    AccountStatus,
    ProjectStatus,
    ResponsibilityCode,
    RoleCode,
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class SelectProjectRequest(BaseModel):
    project_id: int = Field(gt=0)
    # When provided, the caller's previous session is revoked so project switches do not leak live refresh tokens.
    refresh_token: str | None = Field(default=None, max_length=512)


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    display_name: str | None
    account_status: AccountStatus


class ProjectSummary(BaseModel):
    id: int
    code: str
    name: str
    status: ProjectStatus
    role: RoleCode
    responsibilities: list[ResponsibilityCode]
    is_owner: bool
    # Effective permissions so clients do not duplicate the policy matrix.
    permissions: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str
    user: UserResponse
    projects: list[ProjectSummary]
    active_project: ProjectSummary | None


class CurrentSessionResponse(BaseModel):
    user: UserResponse
    projects: list[ProjectSummary]
    active_project: ProjectSummary | None
