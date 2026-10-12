"""Simulator runs record which ADS drives the ego (chosen on the Test Suite screen, sent in run.assign).

Revision ID: 0012_run_ads
Revises: 0011_one_bridge_per_project
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_run_ads"
down_revision = "0011_one_bridge_per_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL for runs made before the choice existed: their ego was the no-brain one.
    op.add_column("test_suite_runs", sa.Column("ads", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("test_suite_runs", "ads")
