"""which rules a company's voucher history was read under

Until the connector typed its static-variable dates, TallyPrime answered every
voucher read from the period selected on the PC, so a backfill reached one
year and stored every other year as empty -- covered, with no trade in it.
Existing rows start at 0 so the sync reads those companies again in full,
once, as soon as their connector can reach other years.

Revision ID: b8d41f6e2a97
Revises: a3c7e2d9f814
Create Date: 2026-10-18 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b8d41f6e2a97'
down_revision = 'a3c7e2d9f814'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'company_sync_states',
        sa.Column('history_version', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'sync_runs',
        sa.Column('history_version', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('sync_runs', 'history_version')
    op.drop_column('company_sync_states', 'history_version')
