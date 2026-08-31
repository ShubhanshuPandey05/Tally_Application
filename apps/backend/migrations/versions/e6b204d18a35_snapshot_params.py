"""snapshot params beside the params hash

``snapshots.params_key`` answers "is this the same request?" and nothing else.
That is not enough when the answer is no. A dashboard read carries today's date
in its params, so the key moves at every date rollover -- and a shop whose PC is
switched off overnight opens the app to sales, receivables and payables saying
"your Tally PC is offline" while cash and stock, whose params carry no date,
still show the last figures read. Same connector, same outage, two different
stories on one screen.

Storing the params themselves lets the read path decide whether a snapshot taken
for a neighbouring window may stand in for the one that was never taken.

Nullable, and left null on existing rows on purpose: an unknown params dict is
treated as "not eligible to stand in", and the next successful refresh fills it.

Revision ID: e6b204d18a35
Revises: d5a83e91c7b4
Create Date: 2026-08-31 12:40:11.204518
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e6b204d18a35'
down_revision = 'd5a83e91c7b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('snapshots', sa.Column('params', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('snapshots', 'params')
