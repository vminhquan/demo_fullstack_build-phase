"""Split project access into roles (ADMIN/MEMBER) and responsibilities.

Old role -> new role + responsibilities:
  project creator -> ADMIN + TESTCASE_CREATE, TESTCASE_REVIEW, TESTCASE_SELF_REVIEW
  ADMIN           -> ADMIN
  CREATOR         -> MEMBER + TESTCASE_CREATE
  REVIEWER        -> MEMBER + TESTCASE_REVIEW
  is_active=false -> membership removed

Fresh databases (0001) and legacy UUID databases (0004) already get the new tables from the
current models, so every step below is skipped for them.

The creator's protections (always ADMIN, never removed) are enforced by the backend policies in
app/shared/domain/policies.py, not by database triggers.

Revision ID: 0007_roles_responsibilities
Revises: 0006_suite_item_trigger
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.shared.infrastructure.models import (
    Base,
    ProjectUserResponsibility,
    Responsibility,
    Role,
)

revision = "0007_roles_responsibilities"
down_revision = "0006_suite_item_trigger"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {
        row.column_name
        for row in bind.execute(
            sa.text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table
                """
            ),
            {"table": table},
        )
    }


def upgrade() -> None:
    bind = op.get_bind()
    # checkfirst: only missing tables are created; the after_create hooks seed roles/responsibilities.
    Base.metadata.create_all(
        bind,
        tables=[Role.__table__, Responsibility.__table__, ProjectUserResponsibility.__table__],
    )

    if "role" in _columns(bind, "project_users"):
        op.execute("DELETE FROM project_users WHERE is_active = false")
        op.execute("ALTER TABLE project_users ADD COLUMN role_code VARCHAR(32)")
        op.execute(
            "UPDATE project_users SET role_code = CASE WHEN role::text = 'ADMIN' THEN 'ADMIN' ELSE 'MEMBER' END"
        )
        op.execute(
            """
            INSERT INTO project_user_responsibilities (project_id, user_id, responsibility_code, assigned_by)
            SELECT project_id, user_id,
                   CASE role::text WHEN 'CREATOR' THEN 'TESTCASE_CREATE' ELSE 'TESTCASE_REVIEW' END,
                   added_by
            FROM project_users
            WHERE role::text IN ('CREATOR', 'REVIEWER')
            """
        )
        op.execute("DROP INDEX IF EXISTS idx_project_users_user_active")
        op.execute("ALTER TABLE project_users DROP COLUMN role, DROP COLUMN is_active")
        # The archived legacy_uuid_0004 schema may still use the enum; keep it in that case.
        op.execute(
            """
            DO $$
            BEGIN
              DROP TYPE IF EXISTS role_code;
            EXCEPTION WHEN dependent_objects_still_exist THEN
              NULL;
            END $$;
            """
        )
        op.execute("ALTER TABLE project_users ALTER COLUMN role_code SET NOT NULL")
        op.execute(
            """
            ALTER TABLE project_users
            ADD CONSTRAINT project_users_role_code_fkey FOREIGN KEY (role_code) REFERENCES roles (code)
            """
        )
        op.execute("CREATE INDEX ix_project_users_role_code ON project_users (role_code)")
        op.execute("CREATE INDEX idx_project_users_user ON project_users (user_id)")

        # The creator is always an admin member with every responsibility.
        op.execute(
            """
            INSERT INTO project_users (project_id, user_id, role_code, added_by)
            SELECT id, created_by, 'ADMIN', created_by FROM projects
            ON CONFLICT (project_id, user_id) DO UPDATE SET role_code = 'ADMIN'
            """
        )
        op.execute(
            """
            INSERT INTO project_user_responsibilities (project_id, user_id, responsibility_code, assigned_by)
            SELECT p.id, p.created_by, r.code, p.created_by
            FROM projects p CROSS JOIN responsibilities r
            ON CONFLICT DO NOTHING
            """
        )

    if "deleted_at" not in _columns(bind, "projects"):
        op.execute(
            """
            ALTER TABLE projects
            ADD COLUMN deleted_at TIMESTAMPTZ,
            ADD COLUMN deleted_by BIGINT REFERENCES users (id) ON DELETE RESTRICT
            """
        )
        op.execute("CREATE INDEX ix_projects_deleted_at ON projects (deleted_at)")


def downgrade() -> None:
    # CREATOR/REVIEWER cannot be reconstructed faithfully from responsibilities (a member may hold
    # both or neither), and removed inactive memberships are gone.
    raise NotImplementedError("0007_roles_responsibilities is irreversible")
