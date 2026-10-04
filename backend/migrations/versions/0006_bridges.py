"""Scenario Forge Bridge: installs, project connections and one-time pair codes.

Revision ID: 0006_bridges
Revises: 0005_builder_sessions
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_bridges'
down_revision = '0005_builder_sessions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('bridges',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('uid', sa.String(length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('hostname', sa.String(length=255), server_default='', nullable=False),
    sa.Column('os', sa.String(length=120), server_default='', nullable=False),
    sa.Column('bridge_version', sa.String(length=32), server_default='', nullable=False),
    sa.Column('carla_host', sa.String(length=255), nullable=True),
    sa.Column('carla_port', sa.Integer(), nullable=True),
    sa.Column('carla_reachable', sa.Boolean(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uid'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('bridge_connections',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('uid', sa.String(length=32), nullable=False),
    sa.Column('bridge_id', sa.BigInteger(), nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('paired_by', sa.BigInteger(), nullable=False),
    sa.Column('paired_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['bridge_id'], ['bridges.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['paired_by'], ['users.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uid'),
    sa.UniqueConstraint('bridge_id', 'project_id', name='uq_bridge_connection_bridge_project')
    )
    op.create_index(op.f('ix_bridge_connections_bridge_id'), 'bridge_connections', ['bridge_id'], unique=False)
    op.create_index(op.f('ix_bridge_connections_project_id'), 'bridge_connections', ['project_id'], unique=False)
    op.create_table('bridge_pair_codes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('connection_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id']),
    sa.ForeignKeyConstraint(['connection_id'], ['bridge_connections.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_bridge_pair_code_hash_active', 'bridge_pair_codes', ['code_hash', 'expires_at'], unique=False)
    op.create_index(op.f('ix_bridge_pair_codes_project_id'), 'bridge_pair_codes', ['project_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_bridge_pair_codes_project_id'), table_name='bridge_pair_codes')
    op.drop_index('idx_bridge_pair_code_hash_active', table_name='bridge_pair_codes')
    op.drop_table('bridge_pair_codes')
    op.drop_index(op.f('ix_bridge_connections_project_id'), table_name='bridge_connections')
    op.drop_index(op.f('ix_bridge_connections_bridge_id'), table_name='bridge_connections')
    op.drop_table('bridge_connections')
    op.drop_table('bridges')
