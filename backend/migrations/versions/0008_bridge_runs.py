"""Simulator Runner runs executed by a Bridge.

test_suite_runs: suite_id optional, bridge_connection_id, dispatched_at, accepted_at.
bridges.online_since: presence shared by every backend process.

Revision ID: 0008_bridge_runs
Revises: 0007_flat_test_cases
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_bridge_runs"
down_revision = "0007_flat_test_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("test_suite_runs", "suite_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("test_suite_runs", sa.Column("bridge_connection_id", sa.BigInteger(), nullable=True))
    op.add_column("test_suite_runs", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_suite_runs", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "test_suite_runs_bridge_connection_id_fkey", "test_suite_runs", "bridge_connections",
        ["bridge_connection_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_test_suite_runs_bridge_connection_id", "test_suite_runs", ["bridge_connection_id"])
    op.add_column("bridges", sa.Column("online_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bridges", "online_since")
    op.drop_index("ix_test_suite_runs_bridge_connection_id", table_name="test_suite_runs")
    op.drop_constraint("test_suite_runs_bridge_connection_id_fkey", "test_suite_runs", type_="foreignkey")
    op.drop_column("test_suite_runs", "accepted_at")
    op.drop_column("test_suite_runs", "dispatched_at")
    op.drop_column("test_suite_runs", "bridge_connection_id")
    op.execute("DELETE FROM test_suite_runs WHERE suite_id IS NULL")
    op.alter_column("test_suite_runs", "suite_id", existing_type=sa.BigInteger(), nullable=False)
