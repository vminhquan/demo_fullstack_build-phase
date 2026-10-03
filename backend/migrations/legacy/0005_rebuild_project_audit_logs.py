"""Rebuild audit logs with project-scoped BIGINT identifiers.

Revision ID: 0005_rebuild_project_audit_logs
Revises: 0004_project_scope_bigint
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.shared.infrastructure.models import AuditLog

revision = "0005_rebuild_project_audit_logs"
down_revision = "0004_project_scope_bigint"
branch_labels = None
depends_on = None

LEGACY_SCHEMA = "legacy_uuid_0004"


def _table_exists(bind: sa.Connection, schema: str, table: str) -> bool:
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
                """
            ),
            {"schema": schema, "table": table},
        ).scalar_one_or_none()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        row.column_name: row.data_type
        for row in bind.execute(
            sa.text(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'audit_logs'
                """
            )
        )
    }

    # Fresh databases already receive the current AuditLog table from 0001.
    if columns.get("project_id") == "bigint" and columns.get("actor_user_id") == "bigint":
        return

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {LEGACY_SCHEMA}")
    legacy_table = "audit_logs"
    if _table_exists(bind, LEGACY_SCHEMA, legacy_table):
        legacy_table = "audit_logs_pre_bigint_0005"

    if _table_exists(bind, "public", "audit_logs"):
        if legacy_table != "audit_logs":
            op.execute(
                f'ALTER TABLE public.audit_logs RENAME TO "{legacy_table}"'
            )
        op.execute(
            f'ALTER TABLE public."{legacy_table}" SET SCHEMA {LEGACY_SCHEMA}'
        )

    AuditLog.__table__.create(bind, checkfirst=True)

    # Keep legacy records queryable in the backup schema and copy their common
    # fields into the live table. UUID entity references cannot be losslessly
    # converted after 0004, so they are retained in after_data for traceability.
    if not _table_exists(bind, LEGACY_SCHEMA, legacy_table):
        return

    has_legacy_users = _table_exists(bind, LEGACY_SCHEMA, "users")
    actor_join = ""
    actor_id = "NULL"
    if has_legacy_users:
        actor_join = f"""
            LEFT JOIN {LEGACY_SCHEMA}.users legacy_user
              ON legacy_user.id = old.actor_user_id
            LEFT JOIN public.users mapped_user
              ON mapped_user.email = legacy_user.email
        """
        actor_id = "mapped_user.id"

    bind.execute(
        sa.text(
            f"""
            INSERT INTO public.audit_logs (
                project_id,
                actor_user_id,
                action,
                entity_type,
                entity_id,
                entity_version_id,
                request_id,
                ip,
                user_agent,
                before_data,
                after_data,
                created_at
            )
            SELECT
                (SELECT id FROM public.projects ORDER BY id LIMIT 1),
                {actor_id},
                old.action,
                old.entity_type,
                NULL,
                NULL,
                old.request_id::text,
                old.ip,
                old.user_agent,
                old.before_data,
                COALESCE(old.after_data, '{{}}'::jsonb)
                    || jsonb_strip_nulls(
                        jsonb_build_object(
                            '_legacy_entity_id', old.entity_id::text,
                            '_legacy_entity_version_id', old.entity_version_id::text
                        )
                    ),
                old.created_at
            FROM {LEGACY_SCHEMA}."{legacy_table}" old
            {actor_join}
            ORDER BY old.created_at, old.id
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "0005 retains the UUID audit table in legacy_uuid_0004; restore it explicitly after backing up new audit records"
    )
