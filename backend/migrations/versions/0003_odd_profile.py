"""Project ODD declaration and cached map profiles of CARLA catalog snapshots.

Revision ID: 0003_odd_profile
Revises: 0002_carla_catalog
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003_odd_profile'
down_revision = '0002_carla_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('odd_profiles',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('declaration', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id']),
    sa.PrimaryKeyConstraint('project_id')
    )
    op.add_column('carla_catalog_snapshots', sa.Column('map_profile', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade():
    op.drop_column('carla_catalog_snapshots', 'map_profile')
    op.drop_table('odd_profiles')
