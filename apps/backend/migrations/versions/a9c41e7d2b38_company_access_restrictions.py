"""what a staff member may see inside a company

Adds ``company_access.restrictions``: the reports, ledger groups and stock
groups an admin has switched off for one person in one company, sealed with
that company's data key. ``NULL`` -- every existing row -- means nothing is
switched off, so this changes no one's access on upgrade.

Revision ID: a9c41e7d2b38
Revises: a4c7e19b2d58
Create Date: 2026-11-04 21:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a9c41e7d2b38'
down_revision = 'a4c7e19b2d58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('company_access', schema=None) as batch_op:
        batch_op.add_column(sa.Column('restrictions', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('company_access', schema=None) as batch_op:
        batch_op.drop_column('restrictions')
