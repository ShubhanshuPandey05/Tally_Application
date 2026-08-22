"""Request and response models for the public API.

Kept separate from the ORM so the wire shape is a deliberate choice rather than
whatever the database happens to look like -- and so a column rename cannot
silently become a breaking API change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

from ..db.models import (
    Company,
    Connector,
    ConnectorStatus,
    OrgStatus,
    Role,
    as_utc,
)
from ..services.entitlements import Entitlement


def _as_utc(value: Any) -> Any:
    """``as_utc`` for a nullable field, leaving anything else to pydantic."""
    return as_utc(value) if isinstance(value, datetime) else value


#: A timestamp guaranteed to reach the app with an explicit UTC offset.
#:
#: SQLite hands back naive datetimes even from a ``DateTime(timezone=True)``
#: column, and a naive ISO string is not a neutral one: Dart's
#: ``DateTime.parse`` reads an offset-less string as **local** time, so
#: ``.toLocal()`` becomes a no-op and the value lands wrong by exactly the
#: device's UTC offset. In India that rendered a connector seen one second ago
#: as "seen 5 hours ago". Everything is stored as UTC, so stamping UTC on the
#: way out states what is already true.
UtcDatetime = Annotated[datetime, BeforeValidator(_as_utc)]

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    #: 10 rather than 8: this guards a company's complete financial history, and
    #: the app offers a password manager anyway.
    password: str = Field(min_length=10, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    org_name: str = Field(min_length=1, max_length=200)
    device_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    device_name: str | None = Field(default=None, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class SubscriptionResponse(BaseModel):
    """What this business is entitled to, as the app needs to render it.

    Sent on every ``/auth/me`` rather than on its own endpoint, because the app
    needs it at exactly the moment it already asks who the user is — on cold
    start, before it decides which controls to draw. A separate call would be a
    second thing to keep in sync and a window in which the two disagree.

    ``allows_changes`` is sent as a resolved boolean instead of leaving the app
    to work it out from status and expiry. The rule is the backend's, it is what
    the API actually enforces, and a client re-deriving it is a client that will
    eventually derive it differently.

    The usage counts are here so the app can say "2 of 3 companies" before the
    third one is refused. Being told a limit exists only at the moment you hit
    it is the worst possible time to learn about it.
    """

    status: OrgStatus
    is_expired: bool
    allows_changes: bool
    allows_data: bool
    max_users: int
    max_companies: int
    users_used: int
    companies_used: int
    expires_at: UtcDatetime | None = None
    #: Empty when the account is live. Written for a shop owner, and always
    #: names the way out -- there is no self-service path here by design.
    message: str = ""

    @classmethod
    def build(
        cls, entitlement: Entitlement, *, users_used: int, companies_used: int
    ) -> SubscriptionResponse:
        return cls(
            status=entitlement.status,
            is_expired=entitlement.is_expired,
            allows_changes=entitlement.allows_changes,
            allows_data=entitlement.allows_data,
            max_users=entitlement.max_users,
            max_companies=entitlement.max_companies,
            users_used=users_used,
            companies_used=companies_used,
            expires_at=entitlement.expires_at,
            message=entitlement.blocked_reason,
        )


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None
    org_id: str
    org_name: str
    role: Role
    #: The app routes straight to "set a new password" while this is true, so a
    #: temporary password handed over in person cannot stay in use.
    must_change_password: bool = False
    subscription: SubscriptionResponse


# --------------------------------------------------------------------------
# Team
# --------------------------------------------------------------------------


class CreateMemberRequest(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role: Role = Role.STAFF
    #: Which companies a staff member may see. Ignored for admins, who see all.
    #: Empty is allowed and means "granted nothing yet" -- a deliberate state, so
    #: an admin can create the account now and decide access with the person.
    company_ids: list[str] = Field(default_factory=list)


class MemberResponse(BaseModel):
    """One person in the organisation, as the team screen shows them."""

    id: str
    email: str
    full_name: str | None
    role: Role
    is_active: bool
    must_change_password: bool
    last_login_at: UtcDatetime | None
    #: For an admin this is every company in the org, because that is what they
    #: can actually see -- returning an empty list and letting the app infer
    #: "admin means all" would put the same rule in two places.
    company_ids: list[str]


class MemberCreatedResponse(BaseModel):
    """The one and only time the temporary password is visible."""

    member: MemberResponse
    temporary_password: str


class UpdateMemberRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    #: Replaces the grant list wholesale rather than merging. The screen sends
    #: the full set of ticked boxes, and a merge would make un-ticking impossible.
    company_ids: list[str] | None = None


class ResetMemberPasswordResponse(BaseModel):
    temporary_password: str


class ChangePasswordRequest(BaseModel):
    #: Not required when the account is still on a temporary password: the person
    #: was handed it by someone else, and asking them to retype it adds a step
    #: without adding proof of anything.
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


# --------------------------------------------------------------------------
# Connectors
# --------------------------------------------------------------------------


class CreateConnectorRequest(BaseModel):
    name: str = Field(default="Tally PC", max_length=200)


class ConnectorPairingResponse(BaseModel):
    """Returned exactly once, at creation.

    ``secret`` is never retrievable again -- only its hash is stored. Losing it
    means re-pairing, which is the correct trade: a secret the server can read
    back is a secret a database leak hands to an attacker.
    """

    connector_id: str
    secret: str
    name: str
    pairing_code: str


class ConnectorResponse(BaseModel):
    id: str
    name: str
    status: ConnectorStatus
    online: bool
    tally_online: bool
    last_seen_at: UtcDatetime | None
    hostname: str | None
    os: str | None
    connector_version: str | None
    companies_open: list[str] = Field(default_factory=list)
    company_count: int = 0

    @classmethod
    def build(
        cls,
        connector: Connector,
        *,
        online: bool,
        companies_open: list[str] | None = None,
        company_count: int = 0,
        seen_at: datetime | None = None,
    ) -> ConnectorResponse:
        """``seen_at`` is the live link's last frame, when there is a link.

        The stored ``last_seen_at`` only advances on connect, disconnect and
        Tally status *transitions* -- deliberately, since writing a row per
        heartbeat per connector would be the busiest query in the system. But
        that means a perfectly healthy connector's stored timestamp freezes at
        connect time, and the app counts up from it: "seen 20 minutes ago" over
        a connector that answered a heartbeat two seconds earlier.
        """
        return cls(
            id=connector.id,
            name=connector.name,
            status=connector.status,
            online=online,
            # A connector can be connected to us while Tally itself is closed;
            # the two states drive very different messages in the app.
            tally_online=online and connector.last_tally_online,
            last_seen_at=seen_at or connector.last_seen_at,
            hostname=connector.hostname,
            os=connector.os,
            connector_version=connector.connector_version,
            companies_open=companies_open or [],
            company_count=company_count,
        )


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


class LinkCompanyRequest(BaseModel):
    """Adopt a company that the connector reports as open in Tally."""

    tally_name: str = Field(min_length=1, max_length=300)
    display_name: str | None = Field(default=None, max_length=300)


class CompanyResponse(BaseModel):
    id: str
    name: str
    tally_name: str
    connector_id: str
    base_currency: str
    is_active: bool
    financial_year_from: date | None = None

    @classmethod
    def build(cls, company: Company) -> CompanyResponse:
        return cls(
            id=company.id,
            name=company.label,
            tally_name=company.tally_name,
            connector_id=company.connector_id,
            base_currency=company.base_currency,
            is_active=company.is_active,
            financial_year_from=(
                company.financial_year_from.date() if company.financial_year_from else None
            ),
        )


class DiscoveredCompany(BaseModel):
    tally_name: str
    guid: str | None = None
    linked: bool = False
    company_id: str | None = None


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

FetchModeParam = Literal["auto", "cached", "live"]


class DataEnvelope(BaseModel):
    """Every data response carries its own freshness.

    Non-negotiable in an accounting product: a number with no "as of" is a
    number the owner cannot safely act on.
    """

    data: Any
    meta: dict[str, Any]


class ReportRequest(BaseModel):
    from_date: date
    to_date: date
    mode: FetchModeParam = "auto"
