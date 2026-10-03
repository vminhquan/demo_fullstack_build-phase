from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.dependencies import Principal, current_principal, require
from app.modules.review.schemas import (
    CommentRequest,
    DecisionRequest,
    ReviewCommentResponse,
    ReviewEvidence,
    ReviewResponse,
    SubmitReviewRequest,
)
from app.modules.testcase.service import get_version
from app.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.shared.domain.policies import (
    ensure_can_decide,
    has_permission,
    validate_reject_comment,
    validate_required_review_comment,
)
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import get_session
from app.shared.infrastructure.models import (
    CommentType,
    ReviewComment,
    ReviewDecision,
    ReviewRequest,
    RunResult,
    TestCaseVersion,
    User,
    VersionStatus,
)

router = APIRouter(tags=["reviews"])


def review_scope(actor: Principal) -> int | None:
    """Reviewers see every review; members who submit see only their own (read-only)."""
    if has_permission(actor.permissions, "review:read"):
        return None
    if has_permission(actor.permissions, "testcase:submit_review"):
        return actor.id
    raise Forbidden("Permission 'review:read' is required")


async def serialize_many(session: AsyncSession, reviews: list[ReviewRequest]) -> list[ReviewResponse]:
    if not reviews:
        return []
    version_ids = {review.version_id for review in reviews}
    versions = {
        version.id: version
        for version in await session.scalars(
            select(TestCaseVersion)
            .where(TestCaseVersion.id.in_(version_ids))
            .options(selectinload(TestCaseVersion.test_case))
        )
    }
    user_ids = {review.requested_by for review in reviews} | {
        review.resolved_by for review in reviews if review.resolved_by is not None
    }
    names = {
        row.id: row.display_name or row.email
        for row in await session.execute(select(User.id, User.display_name, User.email).where(User.id.in_(user_ids)))
    }
    evidence: dict[int, ReviewEvidence] = {}
    run_counts: dict[int, int] = {}
    for result in await session.scalars(
        select(RunResult)
        .where(RunResult.test_case_version_id.in_(version_ids))
        .order_by(RunResult.created_at.desc())
    ):
        run_counts[result.test_case_version_id] = run_counts.get(result.test_case_version_id, 0) + 1
        if result.test_case_version_id in evidence:
            continue
        metrics = result.metrics or {}
        min_ttc = metrics.get("min_ttc_seconds")
        evidence[result.test_case_version_id] = ReviewEvidence(
            run_result_id=result.id,
            run_job_id=result.run_job_id,
            verdict=result.verdict,
            collision=bool(metrics.get("collision")) or int(metrics.get("collision_count", 0) or 0) > 0,
            min_ttc_seconds=float(min_ttc) if isinstance(min_ttc, int | float) else None,
            recorded_at=result.created_at,
            total_runs=0,
        )
    for version_id, item in evidence.items():
        item.total_runs = run_counts[version_id]

    responses = []
    for review in reviews:
        version = versions.get(review.version_id)
        case = version.test_case if version else None
        decision_comment = next(
            (item.body for item in reversed(review.comments) if item.comment_type is CommentType.DECISION),
            None,
        )
        responses.append(
            ReviewResponse(
                id=review.id,
                version_id=review.version_id,
                requested_by=review.requested_by,
                requested_at=review.requested_at,
                resolved_by=review.resolved_by,
                resolved_at=review.resolved_at,
                decision=review.decision,
                comments=[
                    ReviewCommentResponse(
                        id=item.id,
                        creator_id=item.creator_id,
                        body=item.body,
                        comment_type=item.comment_type.value,
                        created_at=item.created_at,
                    )
                    for item in review.comments
                ],
                case_id=case.id if case else None,
                case_key=case.case_key if case else None,
                title=case.title if case else None,
                version_no=version.version_no if version else None,
                version_status=version.status if version else None,
                map_code=version.map_code if version else None,
                adversary_type=version.adversary_type if version else None,
                environment_code=version.environment_code if version else None,
                danger_level=version.danger_level if version else None,
                requested_by_name=names.get(review.requested_by),
                resolved_by_name=names.get(review.resolved_by) if review.resolved_by else None,
                decision_comment=decision_comment,
                evidence=evidence.get(review.version_id),
            )
        )
    return responses


async def serialize(session: AsyncSession, review: ReviewRequest) -> ReviewResponse:
    return (await serialize_many(session, [review]))[0]


async def get_review(
    session: AsyncSession,
    project_id: int,
    review_id: int,
    *,
    locked: bool = False,
) -> ReviewRequest:
    statement = (
        select(ReviewRequest)
        .where(ReviewRequest.id == review_id, ReviewRequest.project_id == project_id)
        .options(selectinload(ReviewRequest.comments))
        # Comments are added by FK after the review was loaded; refresh the collection on re-read.
        .execution_options(populate_existing=True)
    )
    if locked:
        statement = statement.with_for_update()
    review = await session.scalar(statement)
    if review is None:
        raise NotFound("Review request not found")
    return review


@router.post(
    "/test-case-versions/{version_id}/submit-review",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_review(
    version_id: int,
    body: SubmitReviewRequest,
    actor: Principal = Depends(require("testcase:submit_review")),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    version = await get_version(session, actor.project_id, version_id, locked=True)
    if version.created_by != actor.id:
        raise Forbidden("Only the version creator can submit it for review")
    if version.status is not VersionStatus.DRAFT:
        raise Conflict("Only DRAFT versions can be submitted")
    if version.xosc_artifact_id is None:
        raise ValidationFailed("An XOSC artifact is required before review")
    existing = await session.scalar(
        select(ReviewRequest)
        .where(
            ReviewRequest.project_id == actor.project_id,
            ReviewRequest.version_id == version.id,
        )
        .with_for_update()
    )
    if existing is not None:
        raise Conflict("A review request already exists for this version")
    version.status = VersionStatus.IN_REVIEW
    version.submitted_at = datetime.now(UTC)
    review = ReviewRequest(
        project_id=actor.project_id,
        version_id=version.id,
        requested_by=actor.id,
    )
    session.add(review)
    await session.flush()
    if body.message and body.message.strip():
        session.add(
            ReviewComment(
                project_id=actor.project_id,
                review_request_id=review.id,
                creator_id=actor.id,
                body=body.message.strip(),
            )
        )
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="REVIEW_SUBMITTED",
        entity_type="TEST_CASE_VERSION",
        entity_id=version.id,
        entity_version_id=version.id,
        before_data={"status": "DRAFT"},
        after_data={"status": "IN_REVIEW", "review_id": review.id},
    )
    await session.commit()
    return await serialize(session, await get_review(session, actor.project_id, review.id))


@router.get("/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    open_only: bool = True,
    resolved_only: bool = False,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewResponse]:
    statement = (
        select(ReviewRequest)
        .where(ReviewRequest.project_id == actor.project_id)
        .options(selectinload(ReviewRequest.comments))
        .order_by(ReviewRequest.requested_at.desc())
    )
    own = review_scope(actor)
    if own is not None:
        statement = statement.where(ReviewRequest.requested_by == own)
    if resolved_only:
        # Decision history: newest decision first.
        statement = statement.where(ReviewRequest.resolved_at.is_not(None)).order_by(None).order_by(
            ReviewRequest.resolved_at.desc()
        )
    elif open_only:
        statement = statement.where(ReviewRequest.resolved_at.is_(None))
    return await serialize_many(session, list((await session.scalars(statement)).all()))


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
async def read_review(
    review_id: int,
    actor: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    own = review_scope(actor)
    review = await get_review(session, actor.project_id, review_id)
    if own is not None and review.requested_by != own:
        raise NotFound("Review request not found")
    return await serialize(session, review)


@router.post("/reviews/{review_id}/comments", response_model=ReviewResponse)
async def comment(
    review_id: int,
    body: CommentRequest,
    actor: Principal = Depends(require("review:comment")),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    review = await get_review(session, actor.project_id, review_id, locked=True)
    if review.resolved_at:
        raise Conflict("Review request is already resolved")
    session.add(
        ReviewComment(
            project_id=actor.project_id,
            review_request_id=review.id,
            creator_id=actor.id,
            body=validate_required_review_comment(body.body, "Comment body is required"),
        )
    )
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action="REVIEW_COMMENTED",
        entity_type="REVIEW_REQUEST",
        entity_id=review.id,
        entity_version_id=review.version_id,
    )
    await session.commit()
    return await serialize(session, await get_review(session, actor.project_id, review.id))


async def decide(
    review_id: int,
    body: DecisionRequest,
    actor: Principal,
    decision: ReviewDecision,
    session: AsyncSession,
) -> ReviewResponse:
    review = await get_review(session, actor.project_id, review_id, locked=True)
    version = await get_version(session, actor.project_id, review.version_id, locked=True)
    if review.resolved_at or version.status is not VersionStatus.IN_REVIEW:
        raise Conflict("Review is no longer open")
    ensure_can_decide(version.created_by, actor.id, actor.permissions)
    if decision is ReviewDecision.REJECTED:
        comment_value = validate_reject_comment(body.comment)
    elif decision is ReviewDecision.EDIT:
        comment_value = validate_required_review_comment(
            body.comment, "An edit request comment is required"
        )
    else:
        comment_value = (body.comment or "").strip()
    before = {"status": version.status.value}
    now = datetime.now(UTC)
    version.status = VersionStatus(decision.value)
    version.decided_at = now
    version.decided_by = actor.id
    review.decision = decision
    review.resolved_by = actor.id
    review.resolved_at = now
    if comment_value:
        session.add(
            ReviewComment(
                project_id=actor.project_id,
                review_request_id=review.id,
                creator_id=actor.id,
                body=comment_value,
                comment_type=CommentType.DECISION,
            )
        )
    action = "REVIEW_EDIT_REQUESTED" if decision is ReviewDecision.EDIT else f"REVIEW_{decision.value}"
    await record_audit(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        action=action,
        entity_type="TEST_CASE_VERSION",
        entity_id=version.id,
        entity_version_id=version.id,
        before_data=before,
        after_data={"status": version.status.value, "review_id": review.id},
    )
    await session.commit()
    return await serialize(session, await get_review(session, actor.project_id, review.id))


@router.post("/reviews/{review_id}/approve", response_model=ReviewResponse)
async def approve(
    review_id: int,
    body: DecisionRequest,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    return await decide(review_id, body, actor, ReviewDecision.APPROVED, session)


@router.post("/reviews/{review_id}/request-edit", response_model=ReviewResponse)
async def request_edit(
    review_id: int,
    body: DecisionRequest,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    return await decide(review_id, body, actor, ReviewDecision.EDIT, session)


@router.post("/reviews/{review_id}/reject", response_model=ReviewResponse)
async def reject(
    review_id: int,
    body: DecisionRequest,
    actor: Principal = Depends(require("review:decide")),
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    return await decide(review_id, body, actor, ReviewDecision.REJECTED, session)
