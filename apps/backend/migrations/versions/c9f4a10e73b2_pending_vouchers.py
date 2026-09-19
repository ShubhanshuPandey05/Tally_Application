"""entries waiting for a PC that was not there

An entry made from a phone used to be lost when the shop's PC was off or
TallyPrime was closed: the form kept what was typed, but closing the app threw
it away. Somebody recording a receipt at a counter should not have to keep the
screen open until a computer in another room comes back.

The queue is on the server and only on the server (decided 2026-09-17). The
obvious alternative -- an outbox on the shop's PC, which is what BizAnalyst
ships as a local SQLite table -- fails at exactly the moment it is needed,
because the PC being switched off is the case the queue exists for. The backend
is the only place the phone can always reach.

Only entries that are *provably* unsent are queued. A write that timed out, or
whose socket died mid-send, may already be in TallyPrime and is never enqueued;
retrying one of those would book the voucher twice with no way to tell
afterwards which attempt landed.

``expires_at`` is not optional. An entry that has waited a week must not post
silently into a period nobody is looking at any more.

Revision ID: c9f4a10e73b2
Revises: b2c81f4e07a9
Create Date: 2026-09-17 16:05:12.447901
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c9f4a10e73b2'
down_revision = 'b2c81f4e07a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_vouchers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=False),
        sa.Column("company_id", sa.String(length=32), nullable=False),
        # Denormalised from the company: the drain finds every entry for a
        # connector that just reconnected without a join, and the row still
        # explains itself if the company is later relinked to another PC.
        sa.Column("connector_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("party_name", sa.String(length=300), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="waiting"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("tally_voucher_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_vouchers_org_id", "pending_vouchers", ["org_id"])
    op.create_index("ix_pending_vouchers_company_id", "pending_vouchers", ["company_id"])
    op.create_index("ix_pending_vouchers_connector_id", "pending_vouchers", ["connector_id"])
    # The drain's only query: everything waiting for one connector, oldest
    # first, so entries reach Tally in the order somebody made them.
    op.create_index(
        "ix_pending_vouchers_drain",
        "pending_vouchers",
        ["connector_id", "state", "created_at"],
    )
    # What the phone asks for.
    op.create_index(
        "ix_pending_vouchers_company", "pending_vouchers", ["company_id", "state"]
    )


def downgrade() -> None:
    op.drop_index("ix_pending_vouchers_company", table_name="pending_vouchers")
    op.drop_index("ix_pending_vouchers_drain", table_name="pending_vouchers")
    op.drop_index("ix_pending_vouchers_connector_id", table_name="pending_vouchers")
    op.drop_index("ix_pending_vouchers_company_id", table_name="pending_vouchers")
    op.drop_index("ix_pending_vouchers_org_id", table_name="pending_vouchers")
    op.drop_table("pending_vouchers")
