"""CARLA catalog snapshots, Agent scenario generations, version -> catalog snapshot link.

Revision ID: 0002_carla_catalog
Revises: 0001_baseline
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002_carla_catalog'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('carla_catalog_snapshots',
    sa.Column('project_id', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.Enum('DEFAULT', 'WORKER', 'IMPORT', name='catalog_source'), nullable=False),
    sa.Column('worker_installation_id', sa.BigInteger(), nullable=True),
    sa.Column('carla_version', sa.String(length=64), nullable=False),
    sa.Column('map_name', sa.String(length=120), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('spawn_point_count', sa.Integer(), nullable=False),
    sa.Column('waypoint_count', sa.Integer(), nullable=False),
    sa.Column('vehicle_count', sa.Integer(), nullable=False),
    sa.Column('walker_count', sa.Integer(), nullable=False),
    sa.Column('catalog', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'content_hash', name='uq_catalog_project_hash', postgresql_nulls_not_distinct=True)
    )
    op.create_index('idx_catalog_project_map', 'carla_catalog_snapshots', ['project_id', 'map_name', 'created_at'], unique=False)
    op.create_index(op.f('ix_carla_catalog_snapshots_project_id'), 'carla_catalog_snapshots', ['project_id'], unique=False)
    op.create_table('scenario_generations',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('catalog_snapshot_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('COMPLETED', 'ACCEPTED', name='generation_status'), nullable=False),
    sa.Column('generation_mode', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('xosc', sa.Text(), nullable=False),
    sa.Column('xosc_sha256', sa.String(length=64), nullable=False),
    sa.Column('accepted_version_id', sa.BigInteger(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['accepted_version_id'], ['test_case_versions.id'], ),
    sa.ForeignKeyConstraint(['catalog_snapshot_id'], ['carla_catalog_snapshots.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_generation_project_created', 'scenario_generations', ['project_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_scenario_generations_project_id'), 'scenario_generations', ['project_id'], unique=False)
    op.add_column('test_case_versions', sa.Column('catalog_snapshot_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_tcv_catalog_snapshot', 'test_case_versions', 'carla_catalog_snapshots', ['catalog_snapshot_id'], ['id'])


def downgrade():
    op.drop_constraint('fk_tcv_catalog_snapshot', 'test_case_versions', type_='foreignkey')
    op.drop_column('test_case_versions', 'catalog_snapshot_id')
    op.drop_index(op.f('ix_scenario_generations_project_id'), table_name='scenario_generations')
    op.drop_index('idx_generation_project_created', table_name='scenario_generations')
    op.drop_table('scenario_generations')
    op.drop_index(op.f('ix_carla_catalog_snapshots_project_id'), table_name='carla_catalog_snapshots')
    op.drop_index('idx_catalog_project_map', table_name='carla_catalog_snapshots')
    op.drop_table('carla_catalog_snapshots')
    for enum_type in ('generation_status', 'catalog_source'):
        op.execute(f"DROP TYPE IF EXISTS {enum_type}")
