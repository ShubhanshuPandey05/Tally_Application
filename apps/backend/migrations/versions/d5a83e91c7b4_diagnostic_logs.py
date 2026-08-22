"""diagnostic logs

Two tables behind the portal's support view.

``server_logs`` takes WARNING and above from the backend itself. Everything the
process logs already lives in an in-memory ring for the live tail; this is the
half that has to survive a redeploy, because an incident is nearly always
reported after the container that produced it is gone.

``connector_logs`` holds what customers' connectors push over the socket they
already keep open. Neither table has a foreign key, deliberately: a log row must
be writable when the thing it describes is exactly what is broken, and the
lines explaining why a connector was revoked must not be deleted along with it.

Both are pruned on a time window by ``services.logs.LogWriter``.

Revision ID: d5a83e91c7b4
Revises: b7e5c1a49f30
Create Date: 2026-08-19 10:22:41.058310
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd5a83e91c7b4'
down_revision = 'b7e5c1a49f30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'server_logs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('logger', sa.String(length=120), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('instance_id', sa.String(length=32), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('traceback', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('server_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_server_logs_level'), ['level'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_server_logs_instance_id'), ['instance_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_server_logs_request_id'), ['request_id'], unique=False
        )
        # The pruning delete and the default listing both walk this.
        batch_op.create_index('ix_server_logs_time', ['created_at'], unique=False)

    op.create_table(
        'connector_logs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('logged_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.String(length=32), nullable=False),
        sa.Column('connector_id', sa.String(length=32), nullable=False),
        sa.Column('session_id', sa.String(length=32), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('logger', sa.String(length=120), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('dropped_before', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('connector_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_connector_logs_org_id'), ['org_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_connector_logs_connector_id'), ['connector_id'], unique=False
        )
        batch_op.create_index(batch_op.f('ix_connector_logs_level'), ['level'], unique=False)
        # The two shapes every read takes: one account's whole fleet, or one
        # machine. Composite rather than two single-column indexes because both
        # queries are "these rows, newest first" and the sort is the expensive
        # half on a table this size.
        batch_op.create_index(
            'ix_connector_logs_org_time', ['org_id', 'created_at'], unique=False
        )
        batch_op.create_index(
            'ix_connector_logs_connector_time', ['connector_id', 'created_at'], unique=False
        )


def downgrade() -> None:
    op.drop_table('connector_logs')
    op.drop_table('server_logs')
