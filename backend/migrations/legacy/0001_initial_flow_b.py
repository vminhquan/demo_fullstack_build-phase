"""Create Scenario Forge Flow B schema.

Revision ID: 0001_initial_flow_b
Revises:
Create Date: 2026-09-29
"""
from alembic import op

from app.shared.infrastructure.models import Base

revision = "0001_initial_flow_b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(op.get_bind())
    op.execute("ALTER TABLE test_case_search_documents ADD COLUMN embedding vector(768)")
    op.execute("""
        CREATE INDEX idx_tc_search_embedding_hnsw
        ON test_case_search_documents USING hnsw (embedding vector_cosine_ops)
        WHERE embedding_status = 'READY'
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_approved_suite_item()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM test_case_versions WHERE id = NEW.test_case_version_id AND status = 'APPROVED') THEN
            RAISE EXCEPTION 'Only APPROVED test case versions can be added to a suite';
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


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_suite_item_approved ON test_suite_items")
    op.execute("DROP FUNCTION IF EXISTS enforce_approved_suite_item")
    Base.metadata.drop_all(op.get_bind())
