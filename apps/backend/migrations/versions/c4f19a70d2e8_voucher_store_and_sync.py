"""voucher store and chunked sync

Adds the row-per-voucher store that a chunked backfill merges into, plus the
bookkeeping a resumable sync needs: per-company cursors, one row per run, and
one row per date slice.

Revision ID: c4f19a70d2e8
Revises: 9b83127c60f8
Create Date: 2026-08-05 11:12:04.911233
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c4f19a70d2e8'
down_revision = '9b83127c60f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'voucher_records',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('company_id', sa.String(length=32), nullable=False),
        sa.Column('record_key', sa.String(length=128), nullable=False),
        sa.Column('voucher_date', sa.Date(), nullable=False),
        sa.Column('alter_id', sa.BigInteger(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('voucher_type', sa.String(length=200), nullable=True),
        sa.Column('is_effective', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'record_key', name='uq_voucher_company_key'),
    )
    with op.batch_alter_table('voucher_records', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_voucher_records_company_id'), ['company_id'], unique=False
        )
        batch_op.create_index(
            'ix_voucher_records_window', ['company_id', 'voucher_date'], unique=False
        )
        batch_op.create_index(
            'ix_voucher_records_alter', ['company_id', 'alter_id'], unique=False
        )

    op.create_table(
        'company_sync_states',
        sa.Column('company_id', sa.String(length=32), nullable=False),
        sa.Column('books_from', sa.Date(), nullable=True),
        sa.Column('backfilled_from', sa.Date(), nullable=True),
        sa.Column('backfilled_to', sa.Date(), nullable=True),
        sa.Column('voucher_alter_id', sa.BigInteger(), nullable=True),
        sa.Column('master_alter_id', sa.BigInteger(), nullable=True),
        sa.Column('last_delta_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_reconcile_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('supports_incremental', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('company_id'),
    )

    op.create_table(
        'sync_runs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('company_id', sa.String(length=32), nullable=False),
        sa.Column('phase', sa.Enum('backfill', 'delta', name='syncphase', native_enum=False, length=20), nullable=False),
        sa.Column('state', sa.Enum('pending', 'running', 'succeeded', 'failed', 'cancelled', name='syncstate', native_enum=False, length=20), nullable=False),
        sa.Column('total_chunks', sa.Integer(), nullable=False),
        sa.Column('completed_chunks', sa.Integer(), nullable=False),
        sa.Column('current_label', sa.String(length=200), nullable=True),
        sa.Column('vouchers_ingested', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('user_message', sa.Text(), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_sync_runs_company_id'), ['company_id'], unique=False
        )
        batch_op.create_index(batch_op.f('ix_sync_runs_state'), ['state'], unique=False)
        batch_op.create_index(
            'ix_sync_runs_company_time', ['company_id', 'started_at'], unique=False
        )

    op.create_table(
        'sync_chunks',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('from_date', sa.Date(), nullable=False),
        sa.Column('to_date', sa.Date(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('state', sa.Enum('pending', 'running', 'succeeded', 'failed', 'cancelled', name='syncstate', native_enum=False, length=20), nullable=False),
        sa.Column('vouchers', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['sync_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'seq', name='uq_sync_chunk_run_seq'),
    )
    with op.batch_alter_table('sync_chunks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sync_chunks_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('sync_chunks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sync_chunks_run_id'))
    op.drop_table('sync_chunks')

    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_sync_runs_company_time')
        batch_op.drop_index(batch_op.f('ix_sync_runs_state'))
        batch_op.drop_index(batch_op.f('ix_sync_runs_company_id'))
    op.drop_table('sync_runs')

    op.drop_table('company_sync_states')

    with op.batch_alter_table('voucher_records', schema=None) as batch_op:
        batch_op.drop_index('ix_voucher_records_alter')
        batch_op.drop_index('ix_voucher_records_window')
        batch_op.drop_index(batch_op.f('ix_voucher_records_company_id'))
    op.drop_table('voucher_records')
