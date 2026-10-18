"""seal customer books at rest under per-company keys

Adds ``company_keys`` -- one data key per company, stored only wrapped by the
master key in AWS KMS -- and turns the columns that hold a copy of somebody's
books into text, which is what a sealed envelope is (see ``core.sealing``).

The conversion keeps every existing value exactly as it was: JSON becomes the
same JSON as text, which the column type still reads. The rows are then sealed
in the background by ``services.resealing`` while the API keeps serving, so
this migration is quick however large the voucher history is.

Downgrade refuses while any sealed row exists: turning ciphertext back into a
JSON column is not a conversion Postgres can do, and dropping ``company_keys``
first would destroy the only way to open those rows.

Revision ID: b8e1d4f27a90
Revises: a3c7e2d9f814
Create Date: 2026-10-17 12:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b8e1d4f27a90'
down_revision = 'a3c7e2d9f814'
branch_labels = None
depends_on = None

#: (table, column, old type, nullable)
_COLUMNS = [
    ('snapshots', 'payload', sa.JSON(), False),
    ('snapshots', 'params', sa.JSON(), True),
    ('voucher_records', 'payload', sa.JSON(), False),
    ('pending_vouchers', 'payload', sa.JSON(), False),
    ('pending_vouchers', 'party_name', sa.String(length=300), True),
]


def upgrade() -> None:
    op.create_table(
        'company_keys',
        sa.Column('company_id', sa.String(length=32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('wrapped_key', sa.Text(), nullable=False),
        sa.Column('master_ref', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('company_id', 'version'),
    )
    for table, column, old_type, nullable in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=old_type,
                type_=sa.Text(),
                existing_nullable=nullable,
                postgresql_using=f'{column}::text',
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, _, _ in _COLUMNS:
        sealed = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} LIKE 'pa1.%'")
        ).scalar()
        if sealed:
            raise RuntimeError(
                f"{table}.{column} holds {sealed} sealed rows; they cannot be turned "
                "back into JSON, and dropping company_keys would make them unreadable"
            )
    for table, column, old_type, nullable in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            using = f'{column}::json' if isinstance(old_type, sa.JSON) else None
            batch.alter_column(
                column,
                existing_type=sa.Text(),
                type_=old_type,
                existing_nullable=nullable,
                postgresql_using=using,
            )
    op.drop_table('company_keys')
