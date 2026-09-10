"""Pair a shop PC by scanning a QR code instead of typing a secret.

The connector registers a claim, draws the code as a QR on its own local page,
and polls for the answer. Somebody in the app scans it, and the pairing
credentials travel through this table over TLS rather than through a person
copying 43 characters off a phone screen onto a Windows keyboard.

Nothing here is a durable record: rows live minutes and are pruned. The table
exists rather than an in-process dictionary because the two halves of a pairing
arrive on different requests, and a backend restart between them would
otherwise strand a connector waiting for an answer that no longer exists.

Both halves of the credential are stored hashed and the delivered secret is
encrypted, so a dump of this table completes no pairing.

Revision ID: a8d3f1c60b57
Revises: f7a92c14b6d0
Create Date: 2026-09-07 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a8d3f1c60b57'
down_revision = 'f7a92c14b6d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'connector_claims',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('hostname', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('os', sa.String(length=100), nullable=False, server_default=''),
        sa.Column(
            'connector_version', sa.String(length=50), nullable=False, server_default=''
        ),
        sa.Column('org_id', sa.String(length=32), nullable=True),
        sa.Column('connector_id', sa.String(length=32), nullable=True),
        sa.Column('secret_encrypted', sa.Text(), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique: a connector that re-registers the same code updates its own row
    # rather than leaving a trail of them for the collector to disambiguate.
    op.create_index(
        'ix_connector_claims_code_hash', 'connector_claims', ['code_hash'], unique=True
    )
    # The pruner deletes on this column and runs on every registration, so it
    # must not be a table scan on a table that a burst of installs can grow.
    op.create_index('ix_connector_claims_expiry', 'connector_claims', ['expires_at'])

    # The other half of pairing by code: "this PC was asked to pair again".
    # Without it the connector's next handshake is just a failed signature,
    # which must never be allowed to mean "show a pairing screen" -- so a
    # deliberate re-pair would be indistinguishable from a server-side fault.
    op.add_column(
        'connectors',
        sa.Column('repair_requested_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('connectors', 'repair_requested_at')
    op.drop_index('ix_connector_claims_expiry', table_name='connector_claims')
    op.drop_index('ix_connector_claims_code_hash', table_name='connector_claims')
    op.drop_table('connector_claims')
