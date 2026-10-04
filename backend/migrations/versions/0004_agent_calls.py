"""Agent call log (valid-rate and cost reports) and generation result cache key.

Revision ID: 0004_agent_calls
Revises: 0003_odd_profile
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_agent_calls'
down_revision = '0003_odd_profile'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_calls',
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_code', sa.String(length=120), nullable=True),
    sa.Column('generation_id', sa.BigInteger(), nullable=True),
    sa.Column('catalog_snapshot_id', sa.BigInteger(), nullable=True),
    sa.Column('generation_mode', sa.String(length=32), nullable=True),
    sa.Column('from_form', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('cache_hit', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('duration_ms', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['catalog_snapshot_id'], ['carla_catalog_snapshots.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['generation_id'], ['scenario_generations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_agent_call_project_created', 'agent_calls', ['project_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_agent_calls_project_id'), 'agent_calls', ['project_id'], unique=False)
    op.add_column('scenario_generations', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.add_column('scenario_generations', sa.Column('cache_hit', sa.Boolean(), server_default='false', nullable=False))
    op.create_index(op.f('ix_scenario_generations_request_hash'), 'scenario_generations', ['request_hash'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_scenario_generations_request_hash'), table_name='scenario_generations')
    op.drop_column('scenario_generations', 'cache_hit')
    op.drop_column('scenario_generations', 'request_hash')
    op.drop_index(op.f('ix_agent_calls_project_id'), table_name='agent_calls')
    op.drop_index('idx_agent_call_project_created', table_name='agent_calls')
    op.drop_table('agent_calls')
