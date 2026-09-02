"""Persistent model.

Tenancy shape: an **Organisation** is the unit of billing and isolation. It owns
Connectors (installed on shop PCs) and Companies (books inside those Tallys).
Users belong to an organisation through a Membership carrying a Role.

Every authorisation decision reduces to "is this company's ``org_id`` the same as
the caller's?", which is a single indexed comparison rather than a join through
connectors -- deliberately, because an authorisation check that is expensive
eventually gets skipped on a hot path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Force a timestamp read back from the database to be timezone-aware.

    SQLite has no timezone type and hands back naive datetimes even from a
    ``DateTime(timezone=True)`` column, so any comparison against an aware
    ``utc_now()`` raises ``TypeError``. Everything is stored as UTC, so
    attaching UTC to a naive value is a restatement of what is already true --
    not a conversion.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Persist an enum by its value, not its Python member name.

    Without this SQLAlchemy stores ``VIEWER``; the API and every client speak
    ``viewer``, so the two would diverge at the database boundary.
    """
    return [member.value for member in enum_cls]


class Base(DeclarativeBase):
    # JSON rather than JSONB keeps SQLite (tests, local dev) and Postgres on the
    # same mapping. Nothing queries inside these columns.
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


# --------------------------------------------------------------------------
# Tenancy and identity
# --------------------------------------------------------------------------


class Role(StrEnum):
    """What a member may do inside one organisation.

    Two roles, and the line between them is "may this person change the shape of
    the account?" -- add a PC, link a company, create a colleague. Everything an
    admin can do is administrative; everything a staff member can do is read.

    Ordered least to most privileged; :meth:`allows` does the comparison so call
    sites never hardcode a set of roles that has to be updated when a role is
    added. Keeping the ordering (rather than an equality check) is what lets a
    third role slot in between later without revisiting every call site.

    Note this is the *intra-organisation* axis only. Reseller and platform
    authority is a separate concern and must not be added to this ladder: a
    partner outranks an admin over the subscription and outranks nobody over the
    books, which is not a thing one ordered enum can express.
    """

    STAFF = "staff"
    ADMIN = "admin"

    def allows(self, required: Role) -> bool:
        order = [Role.STAFF, Role.ADMIN]
        return order.index(self) >= order.index(required)


class PlatformRole(StrEnum):
    """Authority over the *business* of TallyFlow, not over anybody's books.

    Deliberately a second ladder rather than two more rungs on :class:`Role`.
    A channel partner outranks a shop's admin over that shop's subscription and
    outranks nobody at all over its ledgers, and no single ordered enum can say
    that. Keeping them apart is also what makes the dangerous mistake
    impossible: there is no value of :class:`Role` that a tenant could ever hold
    which grants portal access, because portal access is not on that scale.

        PARTNER  sees and manages only the accounts assigned to them
        OWNER    sees every account, and is the only one who can add a partner
    """

    PARTNER = "partner"
    OWNER = "owner"

    def allows(self, required: PlatformRole) -> bool:
        order = [PlatformRole.PARTNER, PlatformRole.OWNER]
        return order.index(self) >= order.index(required)


class PlatformUser(Base, TimestampMixin):
    """Somebody who signs in to the management portal.

    A separate table from :class:`User`, and separate on purpose. These are not
    customers: they approve customers. Sharing one table would mean the tenant
    login path and the portal login path both read the same rows, and a single
    mistake in the first -- a missing filter, a role trusted from a token --
    would be an escalation into every business on the platform rather than a bug
    in one account.

    The two are distinct namespaces, so the same email address may exist on both
    sides. That is correct: a partner who also runs a shop is two accounts.
    """

    __tablename__ = "platform_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[PlatformRole] = mapped_column(
        SAEnum(PlatformRole, native_enum=False, length=20, values_callable=_enum_values),
        # Least privilege, same reasoning as `Membership.role`: a row created
        # without an explicit role must not be the one that can mint more of
        # itself.
        default=PlatformRole.PARTNER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Set when the account was created with a generated password -- including
    #: the bootstrap owner, whose password comes from an environment variable and
    #: is therefore visible in a deployment manifest until it is changed.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OrgStatus(StrEnum):
    """Where an organisation sits with the people who run TallyFlow.

    A signup does not provision itself. Anyone can install the app, create an
    account and sign in -- but until somebody in the management portal says how
    many people and how many companies this business is entitled to, there is
    nothing they can add. That is the whole shape of the product's commercial
    side, and it is one column.

    ``PENDING`` is therefore the *only* correct default. A new organisation that
    defaulted to ``ACTIVE`` would be a fully provisioned account handed to
    whoever filled in the sign-up form, and the approval step would be an
    optional formality that nobody would notice was being skipped.

    ``REJECTED`` is kept rather than deleted: an account that was turned down
    once must not become approvable again just by signing up a second time with
    the same details, and the audit trail has to keep resolving.
    """

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


class Organisation(Base, TimestampMixin):
    """One customer business: the unit of billing, isolation and entitlement.

    The row doubles as the onboarding request. There is deliberately no separate
    ``onboarding_requests`` table -- a request *is* an organisation that nobody
    has approved yet, and modelling it twice would mean two ids for one business
    and a reconciliation step between them that only ever goes wrong.
    """

    __tablename__ = "organisations"
    __table_args__ = (
        # How the portal's default screen reads: everything awaiting a decision,
        # oldest first. Without the index that becomes a full scan as soon as the
        # fleet is interesting.
        Index("ix_organisations_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[OrgStatus] = mapped_column(
        SAEnum(OrgStatus, native_enum=False, length=20, values_callable=_enum_values),
        default=OrgStatus.PENDING,
    )

    #: Ceilings agreed at approval. Zero, not "unlimited", is the right default:
    #: an organisation nobody has approved has been promised nothing, and a
    #: forgotten approval must fail closed rather than hand out a free fleet.
    max_users: Mapped[int] = mapped_column(Integer, default=0)
    max_companies: Mapped[int] = mapped_column(Integer, default=0)

    #: End of the agreed term, or ``None`` for open-ended. Enforced exactly like
    #: suspension, so setting a date is a real control and not a note. There is
    #: no payment gateway in this system -- this is how a term ends.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The platform user who decided. ``SET NULL`` rather than cascade: a partner
    #: leaving must not delete the record of the accounts they onboarded.
    approved_by: Mapped[str | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True
    )
    #: Which channel partner owns this account. Nullable because an account the
    #: platform owner signed up directly belongs to nobody in particular.
    partner_id: Mapped[str | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Internal, portal-only. Never reaches the customer's phone -- it is where
    #: "spoke to Ravi, three shops, wants two more seats in April" lives.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The shared showroom account: real screens, invented books, no Tally PC
    #: anywhere behind it. It is a flag on the organisation rather than a
    #: separate kind of account because every other answer means a second code
    #: path through auth, entitlements and reads -- and a second code path is
    #: where a demo eventually gets served somebody's real figures.
    #:
    #: It is ``ACTIVE`` so that its books can be read, and
    #: :class:`~tally_backend.services.entitlements.Entitlement` refuses every
    #: *change* on the strength of this column: whoever is exploring must not be
    #: able to unlink the demo company, revoke its connector or invite
    #: themselves a colleague on an account thousands of other people also open.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list[Membership]] = relationship(back_populates="organisation")
    connectors: Mapped[list[Connector]] = relationship(back_populates="organisation")
    companies: Mapped[list[Company]] = relationship(back_populates="organisation")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    #: Argon2id. The column is wide enough for a future parameter bump.
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Set when an admin created this account with a temporary password. Until it
    #: is cleared the only thing the session can do is set a new password.
    #:
    #: There is no email delivery in this system, so a new colleague's password
    #: is necessarily handed over in person or over the phone -- which means the
    #: admin knows it. This flag is what stops that being permanent.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    # A plain String column would hand back a bare ``str`` on load, and
    # ``role.allows(...)`` would fail at the authorisation check. ``native_enum``
    # is off so the stored values stay portable strings rather than a Postgres
    # ENUM type that needs a migration every time a role is added.
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=20, values_callable=_enum_values),
        # Least privilege by default: a membership created without an explicit
        # role must never be the one that hands out administrative access.
        default=Role.STAFF,
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    organisation: Mapped[Organisation] = relationship(back_populates="memberships")


class RefreshToken(Base):
    """One row per issued refresh token, per device.

    Stored as a hash: a database leak must not hand out live sessions. Rotation
    is single-use -- see ``services.auth.rotate_refresh_token`` for why replay
    detection revokes the whole family.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: Groups every token descended from one login, so replay can revoke them all.
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    device_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_usable(self) -> bool:
        return self.revoked_at is None and as_utc(self.expires_at) > utc_now()


# --------------------------------------------------------------------------
# Connectors and companies
# --------------------------------------------------------------------------


class ConnectorStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class Connector(Base, TimestampMixin):
    """A connector installation on a customer's Windows PC."""

    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="Tally PC")
    #: Pairing secret, encrypted at rest (see ``core.crypto``). Not hashed: the
    #: connector authenticates by HMAC-ing with this secret, and verifying that
    #: signature requires the same key that produced it. The encryption key lives
    #: in the environment, so a database dump on its own yields nothing usable.
    secret_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[ConnectorStatus] = mapped_column(
        SAEnum(ConnectorStatus, native_enum=False, length=20, values_callable=_enum_values),
        default=ConnectorStatus.PENDING,
    )

    connector_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(200), nullable=True)
    os: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Query manifest from the last handshake, so the API can answer "your
    #: connector is too old for this report" instead of failing obscurely.
    capabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tally_online: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organisation: Mapped[Organisation] = relationship(back_populates="connectors")
    companies: Mapped[list[Company]] = relationship(back_populates="connector")

    def supports(self, query: str) -> bool:
        # An unpaired-but-never-connected connector has no manifest yet; assume
        # capable rather than blocking the first read after install.
        if not self.capabilities:
            return True
        return any(entry.get("name") == query for entry in self.capabilities)


class Company(Base, TimestampMixin):
    """A set of books inside a connector's TallyPrime.

    ``tally_name`` is the exact string Tally knows it by and is what goes into
    every query envelope. It is not unique globally -- two customers both having
    a company called "Trading" is entirely normal -- so it is unique only within
    a connector.
    """

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("connector_id", "tally_name", name="uq_company_connector_name"),
        Index("ix_companies_org_active", "org_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), index=True
    )
    tally_name: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    guid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    financial_year_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    base_currency: Mapped[str] = mapped_column(String(10), default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    organisation: Mapped[Organisation] = relationship(back_populates="companies")
    connector: Mapped[Connector] = relationship(back_populates="companies")

    @property
    def label(self) -> str:
        return self.display_name or self.tally_name


class CompanyAccess(Base, TimestampMixin):
    """One staff member's permission to see one company's books.

    Admins are deliberately absent from this table. Their access follows from
    their role, so granting it per company would mean two sources of truth for
    the same question and a way for an admin to lock themselves out of the
    organisation they administer.

    So the rule is asymmetric and reads exactly as the product describes it:

        admin  ->  every company in the org
        staff  ->  only the companies listed here

    Deny-by-default matters more here than anywhere else in the schema. A staff
    member with no rows sees nothing, so forgetting to grant access shows up
    immediately as an empty list; the opposite default would show a new joiner
    every set of books in the business and nobody would notice.
    """

    __tablename__ = "company_access"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_company_access_user_company"),
        Index("ix_company_access_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    #: Who granted it. "Why can this person see that?" is the first question
    #: asked when a staff member turns out to have seen more than intended.
    granted_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


class Snapshot(Base):
    """Latest known value of one dataset for one company.

    This is what makes the product usable at all. A dashboard open must not cost
    a round trip to a desktop PC that is asleep, behind home broadband, and
    single-threaded -- so reads are served from here and refreshed out of band.

    ``payload`` holds the serialised domain objects exactly as the connector sent
    them. Deliberately opaque: a new dashboard tile is a backend deploy, not a
    migration.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        # params_key is part of the identity, not just a payload field: a
        # January day book and a February one are the same dataset for the same
        # company. Leaving it out of the constraint makes the second window
        # collide with the first and one report answers with the other's data.
        UniqueConstraint(
            "company_id", "dataset", "params_key", name="uq_snapshot_company_dataset_params"
        ),
        Index("ix_snapshots_refresh_scan", "is_stale", "refreshed_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    #: Registered query name, e.g. ``ledgers.list``.
    dataset: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict | list] = mapped_column(JSON)
    #: Params the payload was fetched with, so a request with different params
    #: (a different date range) is not served the wrong snapshot.
    params_key: Mapped[str] = mapped_column(String(64), default="")
    #: The params themselves, beside the hash of them. The hash answers "is this
    #: the same request?" and nothing else, which is not enough when the answer
    #: is no: a dashboard read carries today's date in its params, so at every
    #: date rollover the key moves and a connector that is switched off leaves
    #: sales and receivables with no snapshot to fall back on while cash and
    #: stock -- whose params hold no date -- keep theirs. Reported as "your
    #: Tally PC is offline" on two cards out of six. Keeping the params lets
    #: :meth:`services.reads.ReadService._stand_in` decide whether a snapshot
    #: taken for a neighbouring window may stand in.
    #:
    #: Null on rows written before this column existed; refilled by the next
    #: successful refresh, and treated as "unknown, not eligible" until then.
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    #: Set when a refresh fails, so the UI can say "showing data from 9:15am"
    #: rather than pretending it is current.
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def age_seconds(self) -> float:
        return (utc_now() - as_utc(self.refreshed_at)).total_seconds()


# --------------------------------------------------------------------------
# The voucher store and its sync
# --------------------------------------------------------------------------


class VoucherRecord(Base):
    """One voucher, kept row-per-voucher rather than inside a snapshot blob.

    :class:`Snapshot` answers "what did this dataset look like at 9:15?", which
    is the right shape for a dashboard window and the wrong shape for history. A
    company with four years of books cannot be read in one export -- TallyPrime
    serves one request at a time and the attempt is what freezes the shop's till
    -- so the backfill arrives in date-ordered slices, and slices have to merge
    into something. This table is that something.

    Row-per-voucher is also what makes incremental sync expressible at all.
    Tally hands back "vouchers altered since change id N"; merging that into a
    blob would mean reading megabytes of JSON, splicing, and writing it back on
    every edit, whereas here it is an upsert on ``record_key``.

    ``payload`` is the serialised domain :class:`~tally_core.domain.transactions
    .Voucher`, so the analytics layer parses store rows and connector responses
    with the same code and cannot drift between them.
    """

    __tablename__ = "voucher_records"
    __table_args__ = (
        UniqueConstraint("company_id", "record_key", name="uq_voucher_company_key"),
        # The shape of every read: one company, one date window, newest first.
        Index("ix_voucher_records_window", "company_id", "voucher_date"),
        # How the delta cursor is recomputed after a sync.
        Index("ix_voucher_records_alter", "company_id", "alter_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    #: Tally's GUID where it gives one. Identity has to survive an edit -- a
    #: voucher whose number or date changed is the same voucher, and keying on
    #: either would leave the old copy behind as a duplicate in every total.
    record_key: Mapped[str] = mapped_column(String(128))
    voucher_date: Mapped[date] = mapped_column(Date)
    #: BigInteger because AlterID counts every edit ever made to the books and
    #: a busy company will outgrow 2^31 long before it outgrows this product.
    alter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    #: Denormalised from the payload so the day book can page without parsing.
    voucher_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Cancelled and optional vouchers are stored, never dropped: they belong in
    #: the day book for audit. Analytics filters on this instead.
    is_effective: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SyncPhase(StrEnum):
    """Which half of the sync a run is doing.

    Distinct rather than a flag because they have different costs and the app
    says different things about them: a backfill is a one-off that takes minutes
    and earns a progress bar, a delta is seconds and should stay invisible.
    """

    BACKFILL = "backfill"
    DELTA = "delta"


class SyncState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    #: Ran out of chunks it could complete. Resumable -- the finished slices are
    #: kept, so a retry picks up where the connector dropped rather than
    #: re-reading everything the shop's Tally already gave us.
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {SyncState.SUCCEEDED, SyncState.FAILED, SyncState.CANCELLED}


class CompanySyncState(Base):
    """Where a company's history sync has got to. One row per company.

    Separate from :class:`SyncRun` because a run is an attempt and this is the
    accumulated result of all of them: the cursors survive a failed run, and a
    resumed backfill reads them to know which years it can skip.
    """

    __tablename__ = "company_sync_states"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    #: Earliest date Tally holds books for. The floor of the backfill, and the
    #: reason it is read from Tally rather than assumed: guessing five years
    #: back on a company that opened last year means five wasted exports.
    books_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The contiguous window actually in :class:`VoucherRecord`. A read outside
    #: it must go to Tally; a read inside it is a database query.
    backfilled_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    backfilled_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Highest voucher AlterID ingested. The next delta asks for what is above.
    voucher_alter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Highest master AlterID seen. Masters are small enough to re-read whole,
    #: so this is only used to decide *whether* to re-read them at all.
    master_alter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_delta_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: An AlterID delta reports what changed but not what was *deleted*, so a
    #: recent window is periodically re-read in full and reconciled. Without it
    #: a voucher deleted in Tally would sit in the store forever.
    last_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when Tally does not report change ids at all. The sync then keeps
    #: working, by re-reading a recent date window instead.
    supports_incremental: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @property
    def has_history(self) -> bool:
        return self.backfilled_from is not None and self.backfilled_to is not None

    def covers(self, from_date: date, to_date: date) -> bool:
        """Whether the store can answer a window without touching Tally."""
        if not self.has_history:
            return False
        assert self.backfilled_from is not None and self.backfilled_to is not None
        return self.backfilled_from <= from_date and to_date <= self.backfilled_to


class SyncRun(Base):
    """One attempt at bringing a company's history up to date.

    Exists mostly so the phone has something honest to render. A first sync
    takes minutes, and a spinner for minutes is indistinguishable from a hang --
    so the run counts its chunks up front and reports them, which is what turns
    "please wait" into "reading Oct-Mar 2024, 3 of 8".
    """

    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_company_time", "company_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[SyncPhase] = mapped_column(
        SAEnum(SyncPhase, native_enum=False, length=20, values_callable=_enum_values),
        default=SyncPhase.BACKFILL,
    )
    state: Mapped[SyncState] = mapped_column(
        SAEnum(SyncState, native_enum=False, length=20, values_callable=_enum_values),
        default=SyncState.PENDING,
        index=True,
    )
    #: Known before the first chunk runs, which is what makes the bar determinate.
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    #: Human-readable, written for a shop owner: "Apr - Sep 2024".
    current_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vouchers_ingested: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Refreshed as each chunk completes. A run whose owning backend instance was
    #: killed mid-sync stops updating this, which is how the next sweep tells a
    #: genuinely running sync from an abandoned row holding the company's lock.
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Phrased for the owner; `error` is for the support call.
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set by a cancel request. The runner checks it between chunks rather than
    #: aborting mid-export, because a half-read export still costs Tally the
    #: whole read.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    chunks: Mapped[list[SyncChunk]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="SyncChunk.seq"
    )

    @property
    def progress(self) -> float:
        """0.0 - 1.0. Chunk-counted, so it only ever moves forwards."""
        if self.total_chunks <= 0:
            return 1.0 if self.state is SyncState.SUCCEEDED else 0.0
        return min(self.completed_chunks / self.total_chunks, 1.0)

    @property
    def elapsed_seconds(self) -> float:
        end = as_utc(self.finished_at) if self.finished_at else utc_now()
        return (end - as_utc(self.started_at)).total_seconds()

    @property
    def eta_seconds(self) -> float | None:
        """Projected from chunks already done, or ``None`` before the first one.

        Deliberately not a guess: showing "about 4 minutes left" before a single
        chunk has finished would be a number invented from nothing, and the app
        renders the absence of an estimate rather than a fabricated one.
        """
        if self.state.is_terminal or self.completed_chunks == 0:
            return None
        remaining = self.total_chunks - self.completed_chunks
        if remaining <= 0:
            return 0.0
        return (self.elapsed_seconds / self.completed_chunks) * remaining


class SyncChunk(Base):
    """One date slice of a run: the unit of work, and the unit of resume.

    Slicing is the whole point of the design. A single ``vouchers.list`` over
    four years is one export that TallyPrime may never finish -- and a request
    it is still working on when the deadline passes leaves the gateway busy long
    after nobody is waiting. Eight bounded reads with a pause between them are
    the same data at a load a desktop can actually serve.
    """

    __tablename__ = "sync_chunks"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_sync_chunk_run_seq"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True
    )
    #: Execution order. Newest slice first -- see ``sync.plan_backfill``.
    seq: Mapped[int] = mapped_column(Integer)
    from_date: Mapped[date] = mapped_column(Date)
    to_date: Mapped[date] = mapped_column(Date)
    label: Mapped[str] = mapped_column(String(200), default="")
    state: Mapped[SyncState] = mapped_column(
        SAEnum(SyncState, native_enum=False, length=20, values_callable=_enum_values),
        default=SyncState.PENDING,
    )
    vouchers: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[SyncRun] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


class AuditLog(Base):
    """Who read what, when.

    Required by CLAUDE.md ("Audit every request"). Reads are audited, not just
    writes, because the sensitive act in a read-only accounting product *is* the
    read.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_org_time", "org_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="ok")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class JobStat(Base):
    """One row per dispatched connector job.

    Kept separate from :class:`AuditLog` because it answers a different question
    -- "is the fleet healthy?" rather than "who saw this data?" -- and is safe to
    aggressively prune.
    """

    __tablename__ = "job_stats"
    __table_args__ = (Index("ix_jobstats_connector_time", "connector_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    connector_id: Mapped[str] = mapped_column(String(32), index=True)
    company_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    query: Mapped[str] = mapped_column(String(100))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


class LogLevel(StrEnum):
    """The subset of Python's levels that is worth storing.

    Stored as a string rather than Python's integer levels because every reader
    of this table is a human or a filter box, and ``30`` is not a level anybody
    types. ``NOTSET`` and ``CRITICAL`` collapse into ``DEBUG`` and ``ERROR`` on
    the way in -- see ``services.logs.normalise_level`` -- so the filter has
    four buckets and not nine.
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ServerLog(Base):
    """A backend log line worth keeping past a restart.

    Only WARNING and above reach this table. Everything the process logs lives
    in an in-memory ring (``services.logs.ServerLogStore``) that the portal
    tails live; this is the half that has to survive a redeploy, because the
    incident is usually reported after the container that produced it is gone.

    Deliberately not a foreign key to anything. A log row must be writable when
    the thing it describes is exactly what is broken -- including a request that
    could not resolve an organisation at all.
    """

    __tablename__ = "server_logs"
    __table_args__ = (Index("ix_server_logs_time", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    level: Mapped[str] = mapped_column(String(10), index=True)
    logger: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    #: Which backend process produced it. Meaningless on a single-instance
    #: deploy and essential the moment there are two: "it only happens on one
    #: instance" is otherwise an unanswerable question.
    instance_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    #: Ties a log line back to the request id already on every API response, so
    #: a customer's screenshot of an error is enough to find the line.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: The formatted traceback, when the record carried one. Separate from
    #: ``message`` so a list view can show one line per row without truncating
    #: the one thing worth reading.
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConnectorLog(Base):
    """A log line pushed from a customer's connector.

    This is the support view's reason to exist: when a shop rings up, the person
    who can help is not in the building, and asking an owner to find
    ``%PROGRAMDATA%\TallyFlow\logs`` and email it is a support call that ends in
    "never mind". So the connector ships its log over the socket it already
    holds open.

    ``org_id`` is denormalised off the connector rather than joined at read
    time. A connector row can be deleted; the logs explaining why it was deleted
    should not go with it, and the org scope is what a partner's visibility is
    checked against on every query.

    Two timestamps, and the distinction matters. ``created_at`` is when *we*
    received the line and is the only safe sort key -- a shop PC with a wrong
    system clock is common. ``logged_at`` is the connector's own clock, kept
    because "the customer's machine thinks it is six hours ago" is a real
    finding rather than noise.
    """

    __tablename__ = "connector_logs"
    __table_args__ = (
        Index("ix_connector_logs_org_time", "org_id", "created_at"),
        Index("ix_connector_logs_connector_time", "connector_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    connector_id: Mapped[str] = mapped_column(String(32), index=True)
    #: The session the line arrived on, so a support view can separate "this
    #: happened before the reconnect" from "after" without guessing at gaps.
    session_id: Mapped[str] = mapped_column(String(32), default="")
    level: Mapped[str] = mapped_column(String(10), index=True)
    logger: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    #: Lines the connector's bounded buffer discarded before this one. Recorded
    #: on the row rather than inferred, because a gap that reads as a quiet
    #: period is how a support session goes down the wrong path.
    dropped_before: Mapped[int] = mapped_column(Integer, default=0)
