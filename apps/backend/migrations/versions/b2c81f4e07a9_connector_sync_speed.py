"""how hard each PC lets us work its TallyPrime

Tuning lived only in backend settings, which are global to the whole fleet. One
shop on an eight-year-old till and another on a modern desktop cannot share a
slice size -- measured on two real companies, one holds 363 vouchers and the
other 5,841 -- and until now helping the slow one meant changing the number for
everybody and redeploying.

The choice is made in the connector's own window, on the machine being
protected, and arrives on every handshake. Stored here so the planner can read
it without waiting for the PC to dial in.

Not nullable, defaulted to 'normal': every connector that exists today is
running what 'normal' describes, so backfilling any other value would silently
re-tune a fleet that nobody asked to change.

Revision ID: b2c81f4e07a9
Revises: a8d3f1c60b57
Create Date: 2026-09-17 10:12:44.881027
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2c81f4e07a9'
down_revision = 'a8d3f1c60b57'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'connectors',
        sa.Column('sync_speed', sa.String(length=10), nullable=False, server_default='normal'),
    )


def downgrade() -> None:
    op.drop_column('connectors', 'sync_speed')
