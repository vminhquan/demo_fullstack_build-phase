from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.infrastructure.models import AuditLog

# Set per HTTP request by the request-id middleware so every audit row can be traced to its request.
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


async def record_audit(
    session: AsyncSession,
    *,
    project_id: int | None,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    entity_version_id: int | None = None,
    request_id: str | None = None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            project_id=project_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_version_id=entity_version_id,
            request_id=request_id or current_request_id.get(),
            before_data=before_data,
            after_data=after_data,
        )
    )
