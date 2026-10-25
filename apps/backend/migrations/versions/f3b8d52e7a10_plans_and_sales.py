"""plans and the record of every plan sold

Two plans, sold for a year at a time. The plan an account is on is kept on the
organisation; every sale -- first activation or renewal -- is a row in
``plan_sales`` with its price and GST copied in, so the partner sales report
never changes when a price or an account's partner does.

Revision ID: f3b8d52e7a10
Revises: e2a7c49d13f6
Create Date: 2026-10-24 10:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f3b8d52e7a10'
down_revision = 'e2a7c49d13f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan', sa.String(length=20), nullable=True))

    op.create_table(
        'plan_sales',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('org_id', sa.String(length=32), nullable=True),
        sa.Column('partner_id', sa.String(length=32), nullable=True),
        sa.Column('sold_by', sa.String(length=32), nullable=True),
        sa.Column('plan', sa.String(length=20), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('price_paise', sa.BigInteger(), nullable=False),
        sa.Column('gst_paise', sa.BigInteger(), nullable=False),
        sa.Column('total_paise', sa.BigInteger(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['partner_id'], ['platform_users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['sold_by'], ['platform_users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_plan_sales_created', 'plan_sales', ['created_at'])
    op.create_index(
        'ix_plan_sales_partner_created', 'plan_sales', ['partner_id', 'created_at']
    )
    op.create_index('ix_plan_sales_org_id', 'plan_sales', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_plan_sales_org_id', table_name='plan_sales')
    op.drop_index('ix_plan_sales_partner_created', table_name='plan_sales')
    op.drop_index('ix_plan_sales_created', table_name='plan_sales')
    op.drop_table('plan_sales')
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('plan')
