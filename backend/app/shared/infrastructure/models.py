from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class TestCaseStatus(str, enum.Enum):
    PENDING = "PENDING"      # waiting for a reviewer (new, or edited since the last decision)
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"    # a reviewer looked at it and declined
    DISCARDED = "DISCARDED"  # the creator removed it before review (restorable while unlocked)


class TestCaseDecisionValue(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DangerLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


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


class CatalogSource(str, enum.Enum):
    DEFAULT = "DEFAULT"  # shipped with the app, visible to every project
    WORKER = "WORKER"  # synced from the user's CARLA by the Worker (doc 18)
    IMPORT = "IMPORT"  # uploaded by a project admin (stand-in for WORKER until the Worker exists)


class GenerationStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    ACCEPTED = "ACCEPTED"


class BuilderSessionStatus(str, enum.Enum):
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"  # every variant saved as a test case
    PARTIAL = "PARTIAL"  # some variants failed
    FAILED = "FAILED"  # no variant could be generated (or the run was interrupted)


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
    """One scenario: generated once by the Agent (or written by hand), edited in place, no versions.

    Editing moves it back to PENDING; once it is put into a simulator run (`locked_at`) it never changes again.
    """

    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("project_id", "case_key", name="uq_test_case_project_key"),
        Index("idx_test_cases_project_updated", "project_id", "updated_at"),
        Index("idx_test_cases_project_status", "project_id", "status"),
        Index("idx_test_cases_project_map", "project_id", "map_code"),
        Index("idx_test_cases_project_danger", "project_id", "danger_level"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    case_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TestCaseStatus] = mapped_column(
        Enum(TestCaseStatus, name="test_case_status"), default=TestCaseStatus.PENDING
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
    # Copy of the artifact hash so listings and config hashes never load the XOSC bytes.
    xosc_sha256: Mapped[str | None] = mapped_column(String(64))
    # CARLA data the XOSC was generated against (NULL for hand-written cases).
    catalog_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("carla_catalog_snapshots.id", name="fk_test_case_catalog_snapshot")
    )
    # Bumped on every edit; runs and decisions record the revision they saw.
    revision: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    # sha256 of the editable configuration + XOSC; compared by decisions, runs and the Bridge.
    config_sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    last_edited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the case is first put into a simulator run; it is read-only from then on.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Test Case Builder session that generated this case (NULL for cases created another way).
    builder_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("builder_sessions.id", ondelete="SET NULL"), index=True
    )
    # Position of the case in its builder session (1..target_count).
    builder_variant_no: Mapped[int | None] = mapped_column(Integer)
    xosc_artifact: Mapped[Artifact | None] = relationship(foreign_keys=[xosc_artifact_id])
    tags: Mapped[list[Tag]] = relationship(secondary="test_case_tags", lazy="selectin")


class TestCaseDecision(IdTimestampMixin, Base):
    """Every approve/reject; a case edited and reviewed again gets one row per decision."""

    __tablename__ = "test_case_decisions"
    __table_args__ = (Index("idx_tc_decisions_project_created", "project_id", "created_at"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    decision: Mapped[TestCaseDecisionValue] = mapped_column(Enum(TestCaseDecisionValue, name="test_case_decision"))
    revision: Mapped[int] = mapped_column(BigInteger)
    config_sha256: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Set when the reviewer clicked "Hoàn tác" right after deciding; the case went back to PENDING.
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CarlaCatalogSnapshot(IdTimestampMixin, Base):
    """One catalog.v1 document: maps, blueprints, spawn points and lane waypoints of a CARLA world."""

    __tablename__ = "carla_catalog_snapshots"
    __table_args__ = (
        Index("idx_catalog_project_map", "project_id", "map_name", "created_at"),
        # Re-sending identical data is idempotent; NULLS NOT DISTINCT so DEFAULT rows (project_id NULL) dedupe too.
        UniqueConstraint("project_id", "content_hash", name="uq_catalog_project_hash", postgresql_nulls_not_distinct=True),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source: Mapped[CatalogSource] = mapped_column(Enum(CatalogSource, name="catalog_source"))
    # Filled by the Worker integration once worker_installations exists (doc 18); no FK until then.
    worker_installation_id: Mapped[int | None] = mapped_column(BigInteger)
    carla_version: Mapped[str] = mapped_column(String(64))
    map_name: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    spawn_point_count: Mapped[int] = mapped_column(Integer)
    waypoint_count: Mapped[int] = mapped_column(Integer)
    vehicle_count: Mapped[int] = mapped_column(Integer)
    walker_count: Mapped[int] = mapped_column(Integer)
    # Full catalog.v1 JSON (hundreds of KB); deferred so listings stay light.
    catalog: Mapped[dict[str, Any]] = mapped_column(JSONB, deferred=True)
    label: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # What the map can host (road types, lanes...), computed once by the Agent from the lane data.
    map_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class OddProfile(TimestampMixin, Base):
    """The project's declared Operational Design Domain: the scope its test cases must cover."""

    __tablename__ = "odd_profiles"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    # OddDeclaration (app/modules/odd/schemas.py): snapshots/maps, road types, weather, lighting, actors, ego, ranges.
    declaration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ScenarioGeneration(IdTimestampMixin, Base):
    """An Agent run (prompt -> grounded XOSC) kept for provenance until a creator saves it as a version."""

    __tablename__ = "scenario_generations"
    __table_args__ = (Index("idx_generation_project_created", "project_id", "created_at"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    prompt: Mapped[str] = mapped_column(Text)
    catalog_snapshot_id: Mapped[int] = mapped_column(ForeignKey("carla_catalog_snapshots.id"))
    status: Mapped[GenerationStatus] = mapped_column(Enum(GenerationStatus, name="generation_status"), default=GenerationStatus.COMPLETED)
    generation_mode: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(120))
    # Agent response without the XOSC text: scenario_ir, grounding, validation, threat_score, warnings...
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    xosc: Mapped[str] = mapped_column(Text, deferred=True)
    xosc_sha256: Mapped[str] = mapped_column(String(64))
    accepted_test_case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of (prompt, catalog content hash, constraints, seed, auto_repair): identical requests reuse the result.
    request_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class BuilderSession(IdTimestampMixin, Base):
    """One Test Case Builder request: a description sent to the Agent on the picked maps; each variant becomes a test case."""

    __tablename__ = "builder_sessions"
    __table_args__ = (Index("idx_builder_session_project_created", "project_id", "created_at"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300))
    # USER = typed by the user; AUTO = summarised from the description.
    title_source: Mapped[str] = mapped_column(String(16), default="USER", server_default="USER")
    prompt: Mapped[str] = mapped_column(Text)
    catalog_source: Mapped[str] = mapped_column(String(16), default="DEFAULT", server_default="DEFAULT")
    # [{"map_code", "ego_vehicle_codes", "adversary_types", "environment_codes", "danger_levels"}] in the user's order.
    maps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    tag_names: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    target_count: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    status: Mapped[BuilderSessionStatus] = mapped_column(
        Enum(BuilderSessionStatus, name="builder_session_status"), default=BuilderSessionStatus.GENERATING
    )
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # [{"variant_no", "map_code", "code", "message"}] of variants the Agent could not generate.
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentCall(IdTimestampMixin, Base):
    """Every Agent request (refine or generate), including failures and cache hits: valid-rate and cost reports."""

    __tablename__ = "agent_calls"
    __table_args__ = (Index("idx_agent_call_project_created", "project_id", "created_at"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))  # REFINE | GENERATE
    # COMPLETED | REJECTED (Agent 422: guardrail, grounding, not a scenario) | FAILED (Agent down / 5xx)
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(120))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("scenario_generations.id", ondelete="SET NULL"))
    catalog_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("carla_catalog_snapshots.id", ondelete="SET NULL"))
    generation_mode: Mapped[str | None] = mapped_column(String(32))
    from_form: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Tag(IdTimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_tag_project_name"),)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))


test_case_tags = Table(
    "test_case_tags",
    Base.metadata,
    Column(
        "test_case_id",
        BigInteger,
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


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
    test_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    suite: Mapped[TestSuite] = relationship(back_populates="items")
    test_case: Mapped[TestCase] = relationship()


class TestSuiteRun(IdTimestampMixin, Base):
    __tablename__ = "test_suite_runs"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # NULL for Simulator Runner runs, which take any approved test cases rather than a saved suite.
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[SuiteRunStatus] = mapped_column(
        Enum(SuiteRunStatus, name="suite_run_status"), default=SuiteRunStatus.QUEUED
    )
    # Bridge that executes the run (Simulator Runner); NULL = legacy worker queue.
    bridge_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("bridge_connections.id", ondelete="SET NULL"), index=True
    )
    # Last time run.assign was sent, and when the Bridge confirmed it with run.accepted.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"))
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
    test_case: Mapped[TestCase] = relationship()
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
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"))
    # Configuration the run actually executed (the case is locked by then, so these equal its current values).
    revision: Mapped[int | None] = mapped_column(BigInteger)
    config_sha256: Mapped[str | None] = mapped_column(String(64))
    xosc_sha256: Mapped[str | None] = mapped_column(String(64))
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
    test_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), unique=True
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


class Bridge(IdTimestampMixin, Base):
    """One Scenario Forge Bridge install (one machine with CARLA). Authenticates with a long-lived device token."""

    __tablename__ = "bridges"

    uid: Mapped[str] = mapped_column(String(32), unique=True)
    # sha256 of the device token; the token itself is only shown to the Bridge once, at first pairing.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    hostname: Mapped[str] = mapped_column(String(255), default="", server_default="")
    os: Mapped[str] = mapped_column(String(120), default="", server_default="")
    bridge_version: Mapped[str] = mapped_column(String(32), default="", server_default="")
    carla_host: Mapped[str | None] = mapped_column(String(255))
    carla_port: Mapped[int | None] = mapped_column(Integer)
    # Last CARLA port probe reported by the Bridge (None = never reported).
    carla_reachable: Mapped[bool | None] = mapped_column(Boolean)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set while a backend process holds the Bridge socket; with last_seen_at it tells every process it is online.
    online_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BridgeConnection(IdTimestampMixin, Base):
    """Link between a Bridge and a project. `uid` is the connection id shared by the Bridge, Backend and Frontend."""

    __tablename__ = "bridge_connections"
    __table_args__ = (UniqueConstraint("bridge_id", "project_id", name="uq_bridge_connection_bridge_project"),)

    uid: Mapped[str] = mapped_column(String(32), unique=True)
    bridge_id: Mapped[int] = mapped_column(ForeignKey("bridges.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    paired_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    bridge: Mapped[Bridge] = relationship(lazy="joined")


class BridgePairCode(IdTimestampMixin, Base):
    """One-time 6-digit OTP shown on the Start up page; the Bridge redeems it with `pair <code>`."""

    __tablename__ = "bridge_pair_codes"
    __table_args__ = (Index("idx_bridge_pair_code_hash_active", "code_hash", "expires_at"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # HMAC-SHA256 of the code; the plain code is never stored.
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("bridge_connections.id", ondelete="SET NULL"))
