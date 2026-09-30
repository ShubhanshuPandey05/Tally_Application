"""per-person daily usage, for the portal's usage view

The audit trail names every screen somebody opens but is kept for two days,
because it grows with traffic. Whether an account is actually using the product
is a question about weeks, so each audited action is also counted into one row
per person per day, kept for months. Counts only -- nothing from anybody's books.

Revision ID: a3c7e2d9f814
Revises: d4e7b2a91c05
Create Date: 2026-09-30 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a3c7e2d9f814'
down_revision = 'd4e7b2a91c05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_activity_days',
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('org_id', sa.String(length=32), nullable=False),
        sa.Column('first_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('events', sa.Integer(), nullable=False),
        sa.Column('dashboard_views', sa.Integer(), nullable=False),
        sa.Column('report_views', sa.Integer(), nullable=False),
        sa.Column('entries_created', sa.Integer(), nullable=False),
        sa.Column('logins', sa.Integer(), nullable=False),
        sa.Column('app_version', sa.String(length=32), nullable=True),
        sa.Column('platform', sa.String(length=16), nullable=True),
        sa.PrimaryKeyConstraint('user_id', 'day'),
    )
    op.create_index(
        'ix_user_activity_org_day', 'user_activity_days', ['org_id', 'day']
    )


def downgrade() -> None:
    op.drop_index('ix_user_activity_org_day', table_name='user_activity_days')
    op.drop_table('user_activity_days')
