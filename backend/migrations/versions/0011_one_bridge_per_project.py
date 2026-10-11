"""One CARLA server (Bridge) per project; one Bridge may still serve many projects.

Projects that had several active connections keep the most recently paired one; the others are revoked.

Revision ID: 0011_one_bridge_per_project
Revises: 0010_carla_map_data
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_one_bridge_per_project"
down_revision = "0010_carla_map_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bridge_connections AS c SET revoked_at = now()
        FROM (
            SELECT id, row_number() OVER (PARTITION BY project_id ORDER BY paired_at DESC, id DESC) AS rank
            FROM bridge_connections WHERE revoked_at IS NULL
        ) AS ranked
        WHERE c.id = ranked.id AND ranked.rank > 1
        """
    )
    op.create_index(
        "uq_bridge_connection_project_active", "bridge_connections", ["project_id"],
        unique=True, postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_bridge_connection_project_active", table_name="bridge_connections")
