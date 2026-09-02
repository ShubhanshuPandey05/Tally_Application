"""Mark an organisation as the shared demo.

The demo account is a real organisation with real rows -- a user, a connector
that was never installed, a company, snapshots, voucher records -- so that every
screen reads it through the same code that reads a customer's books. One column
separates it from the rest, and two rules hang off that column:

* it never grows. ``Entitlement.allows_changes`` is False for a demo, so the
  connector, the company and the team are frozen for everybody who signs in;
* it is never swept. The background refresher skips it, because there is no
  Tally PC on the other end to refresh from.

Defaults to false, which is the only safe direction: an organisation that
became a demo by accident would be one anybody could sign into.

Revision ID: f7a92c14b6d0
Revises: e6b204d18a35
Create Date: 2026-09-02 10:15:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f7a92c14b6d0'
down_revision = 'e6b204d18a35'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organisations',
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('organisations', 'is_demo')
