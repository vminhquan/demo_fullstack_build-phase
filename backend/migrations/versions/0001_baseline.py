"""Baseline schema: every table, index and constraint as of the role/responsibility redesign.

Replaces the former 0001-0007 chain (kept for reference in migrations/legacy/). Unlike those, this
file does not read app models at run time, so it always produces the same schema.

Databases created by the former chain are moved onto this revision by migrations/legacy_bridge.py
(invoked from env.py); they never run this upgrade.

New schema changes: edit the models, then
    alembic revision --autogenerate -m "..."   # review the generated file
    alembic check                               # must report no pending operations

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None

ENUM_TYPES = (
    "account_status", "project_status", "artifact_kind", "version_status", "danger_level",
    "suite_run_status", "review_decision", "run_job_status", "embedding_status", "comment_type",
    "run_verdict", "result_artifact_role",
)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('responsibilities',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('requires_code', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['requires_code'], ['responsibilities.code'], ),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('roles',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('display_name', sa.String(length=160), nullable=True),
    sa.Column('account_status', sa.Enum('PENDING_REGISTRATION', 'ACTIVE', 'SUSPENDED', name='account_status'), server_default='PENDING_REGISTRATION', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_account_status'), 'users', ['account_status'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('projects',
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=250), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'SUSPENDED', 'ARCHIVED', name='project_status'), server_default='ACTIVE', nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_by', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_code'), 'projects', ['code'], unique=True)
    op.create_index(op.f('ix_projects_created_by'), 'projects', ['created_by'], unique=False)
    op.create_index(op.f('ix_projects_deleted_at'), 'projects', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_projects_status'), 'projects', ['status'], unique=False)
    op.create_table('artifacts',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('XOSC', 'RUN_LOG', 'SCREENSHOT', 'REPORT', 'SUITE_EXPORT', 'OTHER', name='artifact_kind'), nullable=False),
    sa.Column('original_name', sa.String(length=512), nullable=True),
    sa.Column('content_type', sa.String(length=255), nullable=True),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('size_bytes >= 0', name='ck_artifact_size_nonnegative'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_artifacts_project_id'), 'artifacts', ['project_id'], unique=False)
    op.create_index(op.f('ix_artifacts_sha256'), 'artifacts', ['sha256'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=True),
    sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('entity_type', sa.String(length=120), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=True),
    sa.Column('entity_version_id', sa.BigInteger(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('before_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_audit_project_actor', 'audit_logs', ['project_id', 'actor_user_id', 'created_at'], unique=False)
    op.create_index('idx_audit_project_entity', 'audit_logs', ['project_id', 'entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_project_id'), 'audit_logs', ['project_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=True),
    sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('refresh_token_hash')
    )
    op.create_index(op.f('ix_auth_sessions_expires_at'), 'auth_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_auth_sessions_project_id'), 'auth_sessions', ['project_id'], unique=False)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('project_users',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('role_code', sa.Enum('ADMIN', 'MEMBER', name='rolecode', native_enum=False, length=32), nullable=False),
    sa.Column('added_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_code'], ['roles.code'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id', 'user_id')
    )
    op.create_index('idx_project_users_user', 'project_users', ['user_id'], unique=False)
    op.create_index(op.f('ix_project_users_role_code'), 'project_users', ['role_code'], unique=False)
    op.create_table('tags',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_tag_project_name')
    )
    op.create_index(op.f('ix_tags_project_id'), 'tags', ['project_id'], unique=False)
    op.create_table('test_cases',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('case_key', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'case_key', name='uq_test_case_project_key')
    )
    op.create_index('idx_test_cases_project_updated', 'test_cases', ['project_id', 'updated_at'], unique=False)
    op.create_index(op.f('ix_test_cases_created_by'), 'test_cases', ['created_by'], unique=False)
    op.create_index(op.f('ix_test_cases_project_id'), 'test_cases', ['project_id'], unique=False)
    op.create_table('test_suites',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=250), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_suite_project_name')
    )
    op.create_index(op.f('ix_test_suites_project_id'), 'test_suites', ['project_id'], unique=False)
    op.create_table('project_user_responsibilities',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('responsibility_code', sa.Enum('TESTCASE_CREATE', 'TESTCASE_REVIEW', 'TESTCASE_SELF_REVIEW', name='responsibilitycode', native_enum=False, length=32), nullable=False),
    sa.Column('assigned_by', sa.BigInteger(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id', 'user_id'], ['project_users.project_id', 'project_users.user_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['responsibility_code'], ['responsibilities.code'], ),
    sa.PrimaryKeyConstraint('project_id', 'user_id', 'responsibility_code')
    )
    op.create_table('test_case_versions',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('test_case_id', sa.BigInteger(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'IN_REVIEW', 'EDIT', 'APPROVED', 'REJECTED', name='version_status'), nullable=False),
    sa.Column('map_code', sa.String(length=120), nullable=False),
    sa.Column('ego_vehicle_code', sa.String(length=255), nullable=False),
    sa.Column('adversary_type', sa.String(length=120), nullable=False),
    sa.Column('environment_code', sa.String(length=120), nullable=False),
    sa.Column('danger_level', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='danger_level'), nullable=False),
    sa.Column('scenario_input', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('xosc_artifact_id', sa.BigInteger(), nullable=True),
    sa.Column('change_note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['xosc_artifact_id'], ['artifacts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('test_case_id', 'version_no', name='uq_test_case_version_no')
    )
    op.create_index('idx_tcv_project_danger', 'test_case_versions', ['project_id', 'danger_level'], unique=False)
    op.create_index('idx_tcv_project_map', 'test_case_versions', ['project_id', 'map_code'], unique=False)
    op.create_index('idx_tcv_project_status', 'test_case_versions', ['project_id', 'status'], unique=False)
    op.create_index(op.f('ix_test_case_versions_project_id'), 'test_case_versions', ['project_id'], unique=False)
    op.create_table('test_suite_runs',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('suite_id', sa.BigInteger(), nullable=False),
    sa.Column('requested_by', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', name='suite_run_status'), nullable=False),
    sa.Column('total_jobs', sa.Integer(), nullable=False),
    sa.Column('completed_jobs', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['suite_id'], ['test_suites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_test_suite_runs_project_id'), 'test_suite_runs', ['project_id'], unique=False)
    op.create_table('review_requests',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('requested_by', sa.BigInteger(), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_by', sa.BigInteger(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision', sa.Enum('APPROVED', 'EDIT', 'REJECTED', name='review_decision'), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['test_case_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id')
    )
    op.create_index(op.f('ix_review_requests_project_id'), 'review_requests', ['project_id'], unique=False)
    op.create_table('run_jobs',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('suite_run_id', sa.BigInteger(), nullable=True),
    sa.Column('test_case_version_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'CLAIMED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', name='run_job_status'), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('worker_id', sa.String(length=255), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=120), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['suite_run_id'], ['test_suite_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_case_version_id'], ['test_case_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'idempotency_key', name='uq_job_project_idempotency')
    )
    op.create_index('idx_jobs_status_queued', 'run_jobs', ['status', 'queued_at'], unique=False)
    op.create_index(op.f('ix_run_jobs_project_id'), 'run_jobs', ['project_id'], unique=False)
    op.create_table('test_case_search_documents',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('embedding_model', sa.String(length=255), nullable=True),
    sa.Column('embedding_status', sa.Enum('PENDING', 'READY', 'FAILED', name='embedding_status'), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('embedding', Vector(768), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['version_id'], ['test_case_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id')
    )
    op.create_index('idx_tc_search_embedding_hnsw', 'test_case_search_documents', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'}, postgresql_where=sa.text("embedding_status = 'READY'"))
    op.create_index(op.f('ix_test_case_search_documents_project_id'), 'test_case_search_documents', ['project_id'], unique=False)
    op.create_table('test_case_version_tags',
    sa.Column('version_id', sa.BigInteger(), nullable=False),
    sa.Column('tag_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['version_id'], ['test_case_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('version_id', 'tag_id')
    )
    op.create_table('test_suite_items',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('suite_id', sa.BigInteger(), nullable=False),
    sa.Column('test_case_version_id', sa.BigInteger(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('added_by', sa.BigInteger(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['suite_id'], ['test_suites.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_case_version_id'], ['test_case_versions.id'], ),
    sa.PrimaryKeyConstraint('suite_id', 'test_case_version_id')
    )
    op.create_index(op.f('ix_test_suite_items_project_id'), 'test_suite_items', ['project_id'], unique=False)
    op.create_table('review_comments',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('review_request_id', sa.BigInteger(), nullable=False),
    sa.Column('creator_id', sa.BigInteger(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('comment_type', sa.Enum('COMMENT', 'DECISION', name='comment_type'), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(body)) > 0', name='ck_review_comment_nonempty'),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['review_request_id'], ['review_requests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_comments_project_id'), 'review_comments', ['project_id'], unique=False)
    op.create_table('run_results',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('run_job_id', sa.BigInteger(), nullable=False),
    sa.Column('test_case_version_id', sa.BigInteger(), nullable=False),
    sa.Column('verdict', sa.Enum('PASS', 'FAIL', 'ERROR', name='run_verdict'), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('scenario_runner_exit_code', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_job_id'], ['run_jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_case_version_id'], ['test_case_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_job_id')
    )
    op.create_index(op.f('ix_run_results_project_id'), 'run_results', ['project_id'], unique=False)
    op.create_table('run_result_artifacts',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('run_result_id', sa.BigInteger(), nullable=False),
    sa.Column('artifact_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('LOG', 'SCREENSHOT', 'REPORT', 'OTHER', name='result_artifact_role'), nullable=False),
    sa.ForeignKeyConstraint(['artifact_id'], ['artifacts.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_result_id'], ['run_results.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_result_id', 'artifact_id')
    )
    op.create_index(op.f('ix_run_result_artifacts_project_id'), 'run_result_artifacts', ['project_id'], unique=False)

    # Lookup rows referenced by project_users.role_code and project_user_responsibilities.
    op.bulk_insert(
        sa.table("roles", sa.column("code"), sa.column("name"), sa.column("description")),
        [
            {"code": "ADMIN", "name": "Quản trị viên", "description": "Quản lý thành viên, nhiệm vụ và Project"},
            {"code": "MEMBER", "name": "Thành viên", "description": "Thành viên của Project"},
        ],
    )
    op.bulk_insert(
        sa.table("responsibilities", sa.column("code"), sa.column("name"), sa.column("description"), sa.column("requires_code")),
        [
            {"code": "TESTCASE_CREATE", "name": "Tạo test case", "description": None, "requires_code": None},
            {"code": "TESTCASE_REVIEW", "name": "Duyệt test case", "description": None, "requires_code": None},
            {"code": "TESTCASE_SELF_REVIEW", "name": "Tự duyệt test case của mình", "description": None, "requires_code": "TESTCASE_REVIEW"},
        ],
    )

    # A suite may only pin APPROVED versions of its own project.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_approved_suite_item()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM test_case_versions
            WHERE id = NEW.test_case_version_id
              AND project_id = NEW.project_id
              AND status = 'APPROVED'
          ) THEN
            RAISE EXCEPTION 'Only APPROVED test case versions in the same project can be added to a suite';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_suite_item_approved
        BEFORE INSERT OR UPDATE ON test_suite_items
        FOR EACH ROW EXECUTE FUNCTION enforce_approved_suite_item();
    """)

def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_suite_item_approved ON test_suite_items")
    op.execute("DROP FUNCTION IF EXISTS enforce_approved_suite_item")
    op.drop_index(op.f('ix_run_result_artifacts_project_id'), table_name='run_result_artifacts')
    op.drop_table('run_result_artifacts')
    op.drop_index(op.f('ix_run_results_project_id'), table_name='run_results')
    op.drop_table('run_results')
    op.drop_index(op.f('ix_review_comments_project_id'), table_name='review_comments')
    op.drop_table('review_comments')
    op.drop_index(op.f('ix_test_suite_items_project_id'), table_name='test_suite_items')
    op.drop_table('test_suite_items')
    op.drop_table('test_case_version_tags')
    op.drop_index(op.f('ix_test_case_search_documents_project_id'), table_name='test_case_search_documents')
    op.drop_index('idx_tc_search_embedding_hnsw', table_name='test_case_search_documents', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'}, postgresql_where=sa.text("embedding_status = 'READY'"))
    op.drop_table('test_case_search_documents')
    op.drop_index(op.f('ix_run_jobs_project_id'), table_name='run_jobs')
    op.drop_index('idx_jobs_status_queued', table_name='run_jobs')
    op.drop_table('run_jobs')
    op.drop_index(op.f('ix_review_requests_project_id'), table_name='review_requests')
    op.drop_table('review_requests')
    op.drop_index(op.f('ix_test_suite_runs_project_id'), table_name='test_suite_runs')
    op.drop_table('test_suite_runs')
    op.drop_index(op.f('ix_test_case_versions_project_id'), table_name='test_case_versions')
    op.drop_index('idx_tcv_project_status', table_name='test_case_versions')
    op.drop_index('idx_tcv_project_map', table_name='test_case_versions')
    op.drop_index('idx_tcv_project_danger', table_name='test_case_versions')
    op.drop_table('test_case_versions')
    op.drop_table('project_user_responsibilities')
    op.drop_index(op.f('ix_test_suites_project_id'), table_name='test_suites')
    op.drop_table('test_suites')
    op.drop_index(op.f('ix_test_cases_project_id'), table_name='test_cases')
    op.drop_index(op.f('ix_test_cases_created_by'), table_name='test_cases')
    op.drop_index('idx_test_cases_project_updated', table_name='test_cases')
    op.drop_table('test_cases')
    op.drop_index(op.f('ix_tags_project_id'), table_name='tags')
    op.drop_table('tags')
    op.drop_index(op.f('ix_project_users_role_code'), table_name='project_users')
    op.drop_index('idx_project_users_user', table_name='project_users')
    op.drop_table('project_users')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_project_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_expires_at'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_audit_logs_project_id'), table_name='audit_logs')
    op.drop_index('idx_audit_project_entity', table_name='audit_logs')
    op.drop_index('idx_audit_project_actor', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_artifacts_sha256'), table_name='artifacts')
    op.drop_index(op.f('ix_artifacts_project_id'), table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_index(op.f('ix_projects_status'), table_name='projects')
    op.drop_index(op.f('ix_projects_deleted_at'), table_name='projects')
    op.drop_index(op.f('ix_projects_created_by'), table_name='projects')
    op.drop_index(op.f('ix_projects_code'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_account_status'), table_name='users')
    op.drop_table('users')
    op.drop_table('roles')
    op.drop_table('responsibilities')
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
