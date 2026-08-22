"""subscriptions and the management portal

Turns the organisation row into the onboarding request and the subscription
record, and adds the separate identity table for the people who approve them.

The delicate part is what happens to organisations that already exist. They were
created before there was anything to approve, so their customers are already
using the product — flipping them to ``pending`` would switch off every account
on the platform at deploy time. They are therefore migrated to ``active``, and
given ceilings from the *current* usage plus headroom rather than from the new
defaults: an existing customer with four companies must not wake up over a limit
they were never told about and unable to link the fifth.

``is_active`` is dropped rather than kept alongside ``status``. It was enforced
nowhere, and leaving two columns that both mean "is this account switched on"
guarantees that one day a check reads the wrong one.

Revision ID: b7e5c1a49f30
Revises: a1d47f2b8e13
Create Date: 2026-08-18 14:05:10.442819
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b7e5c1a49f30'
down_revision = 'a1d47f2b8e13'
branch_labels = None
depends_on = None

#: Headroom added on top of what each existing account already uses. Enough that
#: nobody hits a wall on upgrade day, small enough that the number still means
#: something and gets revisited in the portal.
_MIGRATION_HEADROOM = 2
#: Floors, so an account with nothing linked yet does not migrate to a ceiling
#: of two and immediately behave like an unapproved one.
_MIN_COMPANIES = 3
_MIN_USERS = 5


def upgrade() -> None:
    op.create_table(
        'platform_users',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            'must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    with op.batch_alter_table('platform_users', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_platform_users_email'), ['email'], unique=True
        )

    with op.batch_alter_table('organisations', schema=None) as batch_op:
        # Added with a server default of 'active' so the column can be NOT NULL
        # over existing rows in one statement. The default is dropped below --
        # leaving it in place would mean a *new* signup created by anything that
        # bypasses the ORM would provision itself, which is the one thing this
        # whole change exists to prevent.
        batch_op.add_column(
            sa.Column(
                'status', sa.String(length=20), nullable=False, server_default='active'
            )
        )
        batch_op.add_column(
            sa.Column('max_users', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('max_companies', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('approved_by', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('partner_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch_op.create_index(
            'ix_organisations_status_created', ['status', 'created_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_organisations_partner_id'), ['partner_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_organisations_approved_by', 'platform_users', ['approved_by'], ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_foreign_key(
            'fk_organisations_partner_id', 'platform_users', ['partner_id'], ['id'],
            ondelete='SET NULL',
        )

    # An organisation that had been switched off keeps being switched off. There
    # was no UI for this and the column was enforced nowhere, so in practice
    # every row is active -- but a migration that silently reactivated a
    # deliberately disabled account would be a bad way to find out otherwise.
    organisations = sa.table(
        'organisations', sa.column('status', sa.String), sa.column('is_active', sa.Boolean)
    )
    op.execute(
        organisations.update()
        .where(organisations.c.is_active.is_(False))
        .values(status='suspended')
    )

    # Ceilings from real usage. Written as correlated subqueries so this holds
    # for a database of any size without loading it into the migration.
    op.execute(
        f"""
        UPDATE organisations SET
            max_companies = MAX(
                {_MIN_COMPANIES},
                (SELECT COUNT(*) FROM companies
                  WHERE companies.org_id = organisations.id
                    AND companies.is_active = 1) + {_MIGRATION_HEADROOM}
            ),
            max_users = MAX(
                {_MIN_USERS},
                (SELECT COUNT(*) FROM memberships
                  WHERE memberships.org_id = organisations.id) + {_MIGRATION_HEADROOM}
            ),
            approved_at = created_at
        """
        if op.get_bind().dialect.name == "sqlite"
        else f"""
        UPDATE organisations SET
            max_companies = GREATEST(
                {_MIN_COMPANIES},
                (SELECT COUNT(*) FROM companies
                  WHERE companies.org_id = organisations.id
                    AND companies.is_active = true) + {_MIGRATION_HEADROOM}
            ),
            max_users = GREATEST(
                {_MIN_USERS},
                (SELECT COUNT(*) FROM memberships
                  WHERE memberships.org_id = organisations.id) + {_MIGRATION_HEADROOM}
            ),
            approved_at = created_at
        """
    )

    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('is_active')
        # New rows must fall to the ORM's `PENDING`, never to this.
        batch_op.alter_column('status', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )

    organisations = sa.table(
        'organisations', sa.column('status', sa.String), sa.column('is_active', sa.Boolean)
    )
    # Only a live account survives as active. Pending and rejected both become
    # inactive on the way down, which is the direction that cannot accidentally
    # hand somebody an account nobody ever approved.
    op.execute(
        organisations.update()
        .where(organisations.c.status != 'active')
        .values(is_active=False)
    )

    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_constraint('fk_organisations_partner_id', type_='foreignkey')
        batch_op.drop_constraint('fk_organisations_approved_by', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_organisations_partner_id'))
        batch_op.drop_index('ix_organisations_status_created')
        batch_op.drop_column('notes')
        batch_op.drop_column('partner_id')
        batch_op.drop_column('approved_by')
        batch_op.drop_column('approved_at')
        batch_op.drop_column('expires_at')
        batch_op.drop_column('max_companies')
        batch_op.drop_column('max_users')
        batch_op.drop_column('status')

    with op.batch_alter_table('platform_users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_platform_users_email'))
    op.drop_table('platform_users')
