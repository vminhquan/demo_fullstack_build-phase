"""Test Case Builder sessions and the link from each generated test case to its session.

Revision ID: 0005_builder_sessions
Revises: 0004_agent_calls
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005_builder_sessions'
down_revision = '0004_agent_calls'
branch_labels = None
depends_on = None

STATUS = postgresql.ENUM('GENERATING', 'COMPLETED', 'PARTIAL', 'FAILED', name='builder_session_status', create_type=False)


def upgrade():
    STATUS.create(op.get_bind(), checkfirst=True)
    op.create_table('builder_sessions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('title_source', sa.String(length=16), server_default='USER', nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('catalog_source', sa.String(length=16), server_default='DEFAULT', nullable=False),
    sa.Column('maps', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('tag_names', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('target_count', sa.Integer(), server_default='10', nullable=False),
    sa.Column('status', STATUS, nullable=False),
    sa.Column('succeeded_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id']),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_builder_session_project_created', 'builder_sessions', ['project_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_builder_sessions_project_id'), 'builder_sessions', ['project_id'], unique=False)
    op.add_column('test_cases', sa.Column('builder_session_id', sa.BigInteger(), nullable=True))
    op.add_column('test_cases', sa.Column('builder_variant_no', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_test_cases_builder_session', 'test_cases', 'builder_sessions', ['builder_session_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_test_cases_builder_session_id'), 'test_cases', ['builder_session_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_test_cases_builder_session_id'), table_name='test_cases')
    op.drop_constraint('fk_test_cases_builder_session', 'test_cases', type_='foreignkey')
    op.drop_column('test_cases', 'builder_variant_no')
    op.drop_column('test_cases', 'builder_session_id')
    op.drop_index(op.f('ix_builder_sessions_project_id'), table_name='builder_sessions')
    op.drop_index('idx_builder_session_project_created', table_name='builder_sessions')
    op.drop_table('builder_sessions')
    STATUS.drop(op.get_bind(), checkfirst=True)
