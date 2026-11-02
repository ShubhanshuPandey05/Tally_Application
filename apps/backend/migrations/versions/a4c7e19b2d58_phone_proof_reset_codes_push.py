"""proven phone numbers, password-reset codes and push devices

Three additions that arrive together because they share one Firebase project
and one release of the app:

* ``users.phone_verified_at`` -- when an SMS code proved the number. Null for
  every existing account, which is the truth: none of them was proven.
* ``password_reset_codes`` -- emailed six-digit codes, stored keyed-hashed.
* ``push_devices`` -- FCM registration tokens, one per installed app.

Nothing existing changes meaning, and both tables start empty.

Revision ID: a4c7e19b2d58
Revises: f3b8d52e7a10
Create Date: 2026-11-02 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a4c7e19b2d58'
down_revision = 'f3b8d52e7a10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('phone_verified_at', sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        'password_reset_codes',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('spent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_password_reset_codes_user_id', 'password_reset_codes', ['user_id']
    )

    op.create_table(
        'push_devices',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('token', sa.String(length=512), nullable=False),
        sa.Column('platform', sa.String(length=20), nullable=False),
        sa.Column('app_version', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token', name='uq_push_devices_token'),
    )
    op.create_index('ix_push_devices_user_id', 'push_devices', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_push_devices_user_id', table_name='push_devices')
    op.drop_table('push_devices')
    op.drop_index('ix_password_reset_codes_user_id', table_name='password_reset_codes')
    op.drop_table('password_reset_codes')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('phone_verified_at')
