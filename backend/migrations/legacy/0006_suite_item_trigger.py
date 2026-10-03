"""Always install the project-scoped suite item trigger.

0004 exits early on fresh databases (0001 already creates BIGINT ids), so those databases kept the
0001 trigger that does not check project_id.

Revision ID: 0006_suite_item_trigger
Revises: 0005_rebuild_project_audit_logs
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0006_suite_item_trigger"
down_revision = "0005_rebuild_project_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
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
        """
    )


def downgrade() -> None:
    # The project-scoped check is the intended behaviour; there is nothing safer to restore.
    pass
