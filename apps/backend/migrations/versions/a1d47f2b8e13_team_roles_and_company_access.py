"""team roles and per-company access

Collapses the role ladder to admin/staff and adds the table that decides which
companies a staff member may open.

The role rename is the delicate part. Roles are stored as strings (the enum is
declared ``native_enum=False`` precisely so this is a data update rather than a
Postgres type migration), so existing rows are remapped in place:

    owner       -> admin     the person who created the org keeps full control
    accountant  -> staff     could link companies before; now cannot
    viewer      -> staff

``accountant`` losing the ability to link a company is a real reduction, and it
is deliberate: the product rule is that administrative actions belong to admins.
Any existing accountant who genuinely needs it is promoted by an admin in one
tap, which is safer than silently granting every former accountant full control
of the account.

Staff get no ``company_access`` rows here, which means every demoted member sees
nothing until an admin grants access. That is the intended direction to fail --
the alternative, backfilling a grant for every company, would hand each of them
the whole business on upgrade.

Revision ID: a1d47f2b8e13
Revises: c4f19a70d2e8
Create Date: 2026-08-18 09:41:22.180455
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a1d47f2b8e13'
down_revision = 'c4f19a70d2e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'company_access',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('company_id', sa.String(length=32), nullable=False),
        sa.Column('granted_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'company_id', name='uq_company_access_user_company'),
    )
    with op.batch_alter_table('company_access', schema=None) as batch_op:
        batch_op.create_index('ix_company_access_user', ['user_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_company_access_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_company_access_company_id'), ['company_id'], unique=False
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'must_change_password',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Existing accounts were all created by self-registration, so none of them is
    # on an admin-issued password. Leaving the flag false is correct and keeps
    # every current user out of the forced-reset flow on upgrade.
    memberships = sa.table('memberships', sa.column('role', sa.String))
    op.execute(memberships.update().where(memberships.c.role == 'owner').values(role='admin'))
    op.execute(
        memberships.update()
        .where(memberships.c.role.in_(['accountant', 'viewer']))
        .values(role='staff')
    )


def downgrade() -> None:
    memberships = sa.table('memberships', sa.column('role', sa.String))
    # Staff becomes viewer, not accountant: the downgrade cannot tell which of
    # the two a row started as, and restoring the *lower* of them is the choice
    # that cannot accidentally hand someone back an access level they never had.
    op.execute(memberships.update().where(memberships.c.role == 'staff').values(role='viewer'))
    op.execute(memberships.update().where(memberships.c.role == 'admin').values(role='owner'))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('must_change_password')

    with op.batch_alter_table('company_access', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_access_company_id'))
        batch_op.drop_index(batch_op.f('ix_company_access_user_id'))
        batch_op.drop_index('ix_company_access_user')
    op.drop_table('company_access')
