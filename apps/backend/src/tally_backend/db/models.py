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
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
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
    """What a member may do.

    Ordered least to most privileged; :meth:`allows` does the comparison so call
    sites never hardcode a set of roles that has to be updated when a role is
    added.
    """

    VIEWER = "viewer"
    ACCOUNTANT = "accountant"
    OWNER = "owner"

    def allows(self, required: Role) -> bool:
        order = [Role.VIEWER, Role.ACCOUNTANT, Role.OWNER]
        return order.index(self) >= order.index(required)


class Organisation(Base, TimestampMixin):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

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
        default=Role.VIEWER,
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
