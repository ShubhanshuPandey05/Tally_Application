"""how each PC lets entries from a phone arrive

The connector window has chosen, since write-back shipped, whether entries
arrive as optional vouchers or post straight into the books -- but only the
connector knew. The app now offers a per-entry choice where the PC allows
regular entries, so the setting is reported on every handshake and kept here.

Nullable: a connector built before this does not report it, and not knowing is
read as optional-only, the cautious answer.

Revision ID: d4e7b2a91c05
Revises: c9f4a10e73b2
Create Date: 2026-09-25 13:10:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd4e7b2a91c05'
down_revision = 'c9f4a10e73b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'connectors',
        sa.Column('voucher_entry_mode', sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('connectors', 'voucher_entry_mode')
