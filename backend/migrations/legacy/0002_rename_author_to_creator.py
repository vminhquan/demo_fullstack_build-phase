"""Rename AUTHOR role to CREATOR.

Revision ID: 0002_rename_author_to_creator
Revises: 0001_initial_flow_b
Create Date: 2026-09-30
"""

from alembic import op


revision = "0002_rename_author_to_creator"
down_revision = "0001_initial_flow_b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'role_code' AND e.enumlabel = 'AUTHOR'
          ) AND NOT EXISTS (
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'role_code' AND e.enumlabel = 'CREATOR'
          ) THEN
            ALTER TYPE role_code RENAME VALUE 'AUTHOR' TO 'CREATOR';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    pass
