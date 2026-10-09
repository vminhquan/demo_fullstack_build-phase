"""catalog.v2: the OpenDRIVE text of a CARLA catalog snapshot, stored apart from the JSON document.

Revision ID: 0009_catalog_opendrive
Revises: 0008_bridge_runs
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_catalog_opendrive"
down_revision = "0008_bridge_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("carla_catalog_snapshots", sa.Column("opendrive_xml", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("carla_catalog_snapshots", "opendrive_xml")
