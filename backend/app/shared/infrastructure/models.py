from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}
    # Fetch server/SQL-expression defaults (e.g. updated_at onupdate=now()) via RETURNING. Without this the
    # attribute is expired after flush and reading it in a response triggers a lazy load -> MissingGreenlet.
    __mapper_args__ = {"eager_defaults": True}


class IdTimestampMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoleCode(str, enum.Enum):
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class ResponsibilityCode(str, enum.Enum):
    TESTCASE_CREATE = "TESTCASE_CREATE"
    TESTCASE_REVIEW = "TESTCASE_REVIEW"
    TESTCASE_SELF_REVIEW = "TESTCASE_SELF_REVIEW"


# The project creator starts with every responsibility; an admin may narrow them later.
OWNER_DEFAULT_RESPONSIBILITIES = frozenset(ResponsibilityCode)


class AccountStatus(str, enum.Enum):
    PENDING_REGISTRATION = "PENDING_REGISTRATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class VersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    EDIT = "EDIT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DangerLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    EDIT = "EDIT"
    REJECTED = "REJECTED"


class CommentType(str, enum.Enum):
    COMMENT = "COMMENT"
    DECISION = "DECISION"


class SuiteRunStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunJobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunVerdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


class ArtifactKind(str, enum.Enum):
    XOSC = "XOSC"
    RUN_LOG = "RUN_LOG"
    SCREENSHOT = "SCREENSHOT"
    REPORT = "REPORT"
    SUITE_EXPORT = "SUITE_EXPORT"
    OTHER = "OTHER"


class ResultArtifactRole(str, enum.Enum):
    LOG = "LOG"
    SCREENSHOT = "SCREENSHOT"
    REPORT = "REPORT"
    OTHER = "OTHER"


class EmbeddingStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class User(IdTimestampMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(160))
    account_status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status"),
        default=AccountStatus.PENDING_REGISTRATION,
        server_default=AccountStatus.PENDING_REGISTRATION.value,
        index=True,
    )
    project_links: Mapped[list[ProjectUser]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="ProjectUser.user_id"
    )


class Project(IdTimestampMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"),
        default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
        index=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Deletion is soft so audit logs, run results and artifacts stay traceable.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    user_links: Mapped[list[ProjectUser]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)


class Responsibility(Base):
    __tablename__ = "responsibilities"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    # A responsibility that only makes sense on top of another one (SELF_REVIEW needs REVIEW).
    requires_code: Mapped[str | None] = mapped_column(ForeignKey("responsibilities.code"))


# Rows of `roles` and `responsibilities` are seeded by the baseline migration.


class ProjectUser(Base):
    __tablename__ = "project_users"
    __table_args__ = (Index("idx_project_users_user", "user_id"),)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[RoleCode] = mapped_column(
        "role_code",
        Enum(RoleCode, native_enum=False, length=32),
        ForeignKey("roles.code"),
        index=True,
    )
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    project: Mapped[Project] = relationship(back_populates="user_links")
    user: Mapped[User] = relationship(
        back_populates="project_links", foreign_keys=[user_id]
    )
    responsibility_links: Mapped[list[ProjectUserResponsibility]] = relationship(
        back_populates="member",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="[ProjectUserResponsibility.project_id, ProjectUserResponsibility.user_id]",
    )

    @property
    def responsibilities(self) -> frozenset[ResponsibilityCode]:
        return frozenset(link.responsibility for link in self.responsibility_links)

    def set_responsibilities(self, items: set[ResponsibilityCode], assigned_by: int) -> None:
        kept = [link for link in self.responsibility_links if link.responsibility in items]
        existing = {link.responsibility for link in kept}
        self.responsibility_links = kept + [
            ProjectUserResponsibility(
                project_id=self.project_id,
                user_id=self.user_id,
                responsibility=item,
                assigned_by=assigned_by,
            )
            for item in sorted(items - existing, key=lambda value: value.value)
        ]


class ProjectUserResponsibility(Base):
    __tablename__ = "project_user_responsibilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["project_users.project_id", "project_users.user_id"],
            ondelete="CASCADE",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    responsibility: Mapped[ResponsibilityCode] = mapped_column(
        "responsibility_code",
        Enum(ResponsibilityCode, native_enum=False, length=32),
        ForeignKey("responsibilities.code"),
        primary_key=True,
    )
    assigned_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    member: Mapped[ProjectUser] = relationship(
        back_populates="responsibility_links",
        foreign_keys=[project_id, user_id],
    )


class AuthSession(IdTimestampMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(IdTimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="ck_artifact_size_nonnegative"),)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(Enum(ArtifactKind, name="artifact_kind"))
    original_name: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )


class TestCase(IdTimestampMixin, TimestampMixin, Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("project_id", "case_key", name="uq_test_case_project_key"),
        Index("idx_test_cases_project_updated", "project_id", "updated_at"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    case_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    versions: Mapped[list[TestCaseVersion]] = relationship(
        back_populates="test_case",
        cascade="all, delete-orphan",
        order_by="TestCaseVersion.version_no",
    )


class TestCaseVersion(IdTimestampMixin, Base):
    __tablename__ = "test_case_versions"
    __table_args__ = (
        UniqueConstraint("test_case_id", "version_no", name="uq_test_case_version_no"),
        Index("idx_tcv_project_map", "project_id", "map_code"),
        Index("idx_tcv_project_status", "project_id", "status"),
        Index("idx_tcv_project_danger", "project_id", "danger_level"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(
        Enum(VersionStatus, name="version_status"), default=VersionStatus.DRAFT
    )
    map_code: Mapped[str] = mapped_column(String(120))
    ego_vehicle_code: Mapped[str] = mapped_column(String(255))
    adversary_type: Mapped[str] = mapped_column(String(120))
    environment_code: Mapped[str] = mapped_column(String(120))
    danger_level: Mapped[DangerLevel] = mapped_column(Enum(DangerLevel, name="danger_level"))
    scenario_input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    xosc_artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    test_case: Mapped[TestCase] = relationship(back_populates="versions")
    xosc_artifact: Mapped[Artifact | None] = relationship(foreign_keys=[xosc_artifact_id])
    tags: Mapped[list[Tag]] = relationship(
        secondary="test_case_version_tags", lazy="selectin"
    )


class Tag(IdTimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_tag_project_name"),)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))


test_case_version_tags = Table(
    "test_case_version_tags",
    Base.metadata,
    Column(
        "version_id",
        BigInteger,
        ForeignKey("test_case_versions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class ReviewRequest(IdTimestampMixin, Base):
    __tablename__ = "review_requests"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[int] = mapped_column(ForeignKey("test_case_versions.id"), unique=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[ReviewDecision | None] = mapped_column(
        Enum(ReviewDecision, name="review_decision")
    )
    comments: Mapped[list[ReviewComment]] = relationship(
        back_populates="review_request", cascade="all, delete-orphan"
    )


class ReviewComment(IdTimestampMixin, Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="ck_review_comment_nonempty"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    review_request_id: Mapped[int] = mapped_column(
        ForeignKey("review_requests.id", ondelete="CASCADE")
    )
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    comment_type: Mapped[CommentType] = mapped_column(
        Enum(CommentType, name="comment_type"), default=CommentType.COMMENT
    )
    review_request: Mapped[ReviewRequest] = relationship(back_populates="comments")


class TestSuite(IdTimestampMixin, TimestampMixin, Base):
    __tablename__ = "test_suites"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_suite_project_name"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list[TestSuiteItem]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class TestSuiteItem(Base):
    __tablename__ = "test_suite_items"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[int] = mapped_column(
        ForeignKey("test_suites.id", ondelete="CASCADE"), primary_key=True
    )
    test_case_version_id: Mapped[int] = mapped_column(
        ForeignKey("test_case_versions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    suite: Mapped[TestSuite] = relationship(back_populates="items")
    version: Mapped[TestCaseVersion] = relationship()


class TestSuiteRun(IdTimestampMixin, Base):
    __tablename__ = "test_suite_runs"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[int] = mapped_column(ForeignKey("test_suites.id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[SuiteRunStatus] = mapped_column(
        Enum(SuiteRunStatus, name="suite_run_status"), default=SuiteRunStatus.QUEUED
    )
    total_jobs: Mapped[int] = mapped_column(Integer, default=0)
    completed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs: Mapped[list[RunJob]] = relationship(
        back_populates="suite_run", cascade="all, delete-orphan"
    )


class RunJob(IdTimestampMixin, Base):
    __tablename__ = "run_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_job_project_idempotency"),
        Index("idx_jobs_status_queued", "status", "queued_at"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    suite_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_suite_runs.id", ondelete="CASCADE")
    )
    test_case_version_id: Mapped[int] = mapped_column(ForeignKey("test_case_versions.id"))
    status: Mapped[RunJobStatus] = mapped_column(
        Enum(RunJobStatus, name="run_job_status"), default=RunJobStatus.QUEUED
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    worker_id: Mapped[str | None] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    suite_run: Mapped[TestSuiteRun | None] = relationship(back_populates="jobs")
    version: Mapped[TestCaseVersion] = relationship()
    result: Mapped[RunResult | None] = relationship(
        back_populates="run_job", uselist=False, cascade="all, delete-orphan"
    )


class RunResult(IdTimestampMixin, Base):
    __tablename__ = "run_results"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_job_id: Mapped[int] = mapped_column(
        ForeignKey("run_jobs.id", ondelete="CASCADE"), unique=True
    )
    test_case_version_id: Mapped[int] = mapped_column(ForeignKey("test_case_versions.id"))
    verdict: Mapped[RunVerdict] = mapped_column(Enum(RunVerdict, name="run_verdict"))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    scenario_runner_exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    run_job: Mapped[RunJob] = relationship(back_populates="result")
    artifacts: Mapped[list[RunResultArtifact]] = relationship(
        back_populates="run_result", cascade="all, delete-orphan"
    )


class RunResultArtifact(Base):
    __tablename__ = "run_result_artifacts"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_result_id: Mapped[int] = mapped_column(
        ForeignKey("run_results.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    role: Mapped[ResultArtifactRole] = mapped_column(
        Enum(ResultArtifactRole, name="result_artifact_role")
    )
    run_result: Mapped[RunResult] = relationship(back_populates="artifacts")
    artifact: Mapped[Artifact] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(
            "idx_audit_project_entity",
            "project_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
        Index("idx_audit_project_actor", "project_id", "actor_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    entity_version_id: Mapped[int | None] = mapped_column(BigInteger)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestCaseSearchDocument(Base):
    __tablename__ = "test_case_search_documents"
    __table_args__ = (
        # Approximate nearest-neighbour index for semantic search; only finished embeddings are indexed.
        Index(
            "idx_tc_search_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding_status = 'READY'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="CASCADE"), unique=True
    )
    search_text: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        Enum(EmbeddingStatus, name="embedding_status"), default=EmbeddingStatus.PENDING
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Declared so Alembic autogenerate sees it (otherwise it proposes dropping the column).
    # Deferred: vectors are large and only semantic search reads them.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), deferred=True)
