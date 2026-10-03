from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.shared.domain.errors import NotFound
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.http import content_disposition
from app.shared.infrastructure.models import Artifact

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: int, actor: Principal = Depends(require("testcase:read")), session: AsyncSession = Depends(get_session)) -> Response:
    artifact = await session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.project_id == actor.project_id,
        )
    )
    if artifact is None or artifact.deleted_at is not None: raise NotFound("Artifact not found")
    return Response(artifact.content, media_type=artifact.content_type or "application/octet-stream", headers={"Content-Disposition": content_disposition(artifact.original_name or str(artifact.id))})
