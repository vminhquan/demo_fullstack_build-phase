"""Add the EDIT outcome for review requests.

Revision ID: 0003_add_edit_review_status
Revises: 0002_rename_author_to_creator
Create Date: 2026-09-30
"""

from alembic import op


revision = "0003_add_edit_review_status"
down_revision = "0002_rename_author_to_creator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The two enums are used by existing rows, so extend them instead of recreating them.
    op.execute("ALTER TYPE version_status ADD VALUE IF NOT EXISTS 'EDIT'")
    op.execute("ALTER TYPE review_decision ADD VALUE IF NOT EXISTS 'EDIT'")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'review_comments'
              AND column_name = 'author_id'
          ) THEN
            ALTER TABLE review_comments RENAME COLUMN author_id TO creator_id;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL enum values cannot be safely removed while historical rows may use EDIT.
    # Keeping the enum values makes downgrade non-destructive for audit/history data.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'review_comments'
              AND column_name = 'creator_id'
          ) THEN
            ALTER TABLE review_comments RENAME COLUMN creator_id TO author_id;
          END IF;
        END $$;
        """
    )
