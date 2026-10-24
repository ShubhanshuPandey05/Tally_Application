"""free trial, phone numbers and partner codes

A business whose admin gives an email and a phone number now starts on a free
trial instead of waiting in the portal's queue. The trial's days count from the
first company the business links of its own, not from signup, so two
timestamps are kept on the organisation: when the trial was given, and when its
clock started.

Phone numbers are unique per account, because a trial per phone is what keeps
it from being a trial per email address. Partner codes are unique, set when an
owner creates a partner, and never changed after.

All nullable. Every existing account keeps exactly the state it had: a pending
signup stays pending until its admin adds a phone number at the next sign-in,
and an approved account was never on a trial.

Revision ID: e2a7c49d13f6
Revises: b8e1d4f27a90
Create Date: 2026-10-24 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e2a7c49d13f6'
down_revision = 'b8e1d4f27a90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=20), nullable=True))
        batch_op.create_unique_constraint('uq_users_phone', ['phone'])

    with op.batch_alter_table('platform_users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('partner_code', sa.String(length=20), nullable=True)
        )
        batch_op.create_unique_constraint(
            'uq_platform_users_partner_code', ['partner_code']
        )

    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('trial_granted_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column('trial_started_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('trial_started_at')
        batch_op.drop_column('trial_granted_at')

    with op.batch_alter_table('platform_users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_platform_users_partner_code', type_='unique')
        batch_op.drop_column('partner_code')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_phone', type_='unique')
        batch_op.drop_column('phone')
