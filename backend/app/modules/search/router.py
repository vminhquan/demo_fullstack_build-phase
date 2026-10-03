from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.dependencies import Principal, require
from app.modules.search.schemas import RagSearchRequest, RagSearchResponse
from app.modules.search.service import search
from app.shared.infrastructure.db import get_session

router = APIRouter(prefix="/rag-search", tags=["rag-search"])


@router.post("", response_model=RagSearchResponse)
async def rag_search(
    body: RagSearchRequest,
    actor: Principal = Depends(require("testcase:read")),
    session: AsyncSession = Depends(get_session),
) -> RagSearchResponse:
    return await search(body, session, actor.project_id)
