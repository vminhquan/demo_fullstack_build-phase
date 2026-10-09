"""Shared, compressed map data (waypoints + OpenDRIVE) for CARLA catalog snapshots.

A Town04 snapshot carried ~10 MB of waypoint JSON and 3.4 MB of OpenDRIVE in one row, which the
free Render Postgres did not survive. New snapshots keep only the light JSON and point here.

Revision ID: 0010_carla_map_data
Revises: 0009_catalog_opendrive
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_carla_map_data"
down_revision = "0009_catalog_opendrive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "carla_map_data",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("data_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("waypoints_gz", sa.LargeBinary(), nullable=False),
        sa.Column("opendrive_gz", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "carla_catalog_snapshots",
        sa.Column("map_data_id", sa.BigInteger(), sa.ForeignKey("carla_map_data.id"), nullable=True),
    )
    op.create_index("ix_carla_catalog_snapshots_map_data_id", "carla_catalog_snapshots", ["map_data_id"])


def downgrade() -> None:
    op.drop_index("ix_carla_catalog_snapshots_map_data_id", table_name="carla_catalog_snapshots")
    op.drop_column("carla_catalog_snapshots", "map_data_id")
    op.drop_table("carla_map_data")
