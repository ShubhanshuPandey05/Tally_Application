"""Wire shapes for the management portal.

Kept apart from ``api.schemas`` on purpose. That module is the contract with a
fleet of phones that update on their own schedule and sometimes never; this one
is the contract with a single-page tool served from the same deploy, which
therefore cannot be out of date. Sharing a file would invite a portal-shaped
field into a response the app parses, and the app is the half that cannot be
fixed by redeploying.

Nothing here reaches a customer. That is what makes ``notes``, ``partner_id``
and the usage counters safe to put in one flat object.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from ..db.models import OrgStatus, PlatformRole
from .schemas import UtcDatetime

# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------


class PortalLoginRequest(BaseModel):
    #: A plain string, not ``EmailStr``. A sign-in form has no business
    #: validating the *shape* of what was typed -- the only question is whether
    #: it matches a stored account, and rejecting it earlier just adds a second
    #: way to fail. It also matters concretely here: ``EmailStr`` refuses
    #: reserved TLDs like ``.local``, so a bootstrap owner seeded from an
    #: environment variable could be created and then never able to sign in.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class PortalUserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: PlatformRole
    is_active: bool
    must_change_password: bool
    last_login_at: UtcDatetime | None = None
    #: How many customer accounts this person is responsible for. Shown on the
    #: partner list, which is otherwise a list of names that says nothing.
    accounts: int = 0


class PortalSessionResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: PortalUserResponse


class PortalChangePasswordRequest(BaseModel):
    #: Not required while the account is still on a seeded or issued password,
    #: for the same reason as the tenant flow: somebody else chose it, so
    #: retyping it proves nothing about who is at the keyboard.
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class CreatePartnerRequest(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role: PlatformRole = PlatformRole.PARTNER


class PartnerCreatedResponse(BaseModel):
    """The one and only time the temporary password is visible."""

    partner: PortalUserResponse
    temporary_password: str


class UpdatePartnerRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: PlatformRole | None = None
    is_active: bool | None = None


# --------------------------------------------------------------------------
# Customer accounts
# --------------------------------------------------------------------------


class AccountAdmin(BaseModel):
    """Who to ring about this account."""

    id: str
    email: str
    full_name: str | None
    last_login_at: UtcDatetime | None = None


class AccountResponse(BaseModel):
    """One customer business, as the portal lists and inspects it.

    Usage is sent beside every ceiling rather than only on the detail screen: a
    request to raise a limit is impossible to judge without knowing how much of
    the current one is spent, and "go and look it up" is how limits get raised
    by reflex.
    """

    id: str
    name: str
    status: OrgStatus
    is_expired: bool
    max_users: int
    max_companies: int
    users_used: int
    companies_used: int
    connectors: int
    connectors_online: int
    expires_at: UtcDatetime | None = None
    created_at: UtcDatetime
    approved_at: UtcDatetime | None = None
    approved_by: str | None = None
    approved_by_name: str | None = None
    partner_id: str | None = None
    partner_name: str | None = None
    notes: str | None = None
    #: The organisation's admins, so a decision can be made without a second
    #: request. There are rarely more than a handful.
    admins: list[AccountAdmin] = Field(default_factory=list)


class ApproveAccountRequest(BaseModel):
    """The decision itself: how much of the product this business gets.

    Both ceilings are required, with no default. A portal that pre-filled them
    server-side would make "approve" a single click that silently applied
    somebody's idea of a standard plan — and the numbers are the entire content
    of the decision. The *form* suggests values; the API insists on them.
    """

    max_users: int = Field(ge=1, le=10_000)
    max_companies: int = Field(ge=1, le=10_000)
    expires_at: UtcDatetime | None = None
    #: Which partner owns the relationship. Only an owner may set it to someone
    #: other than themselves.
    partner_id: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


class UpdateAccountRequest(BaseModel):
    """Changing an account after the fact.

    Every field is optional and ``None`` means "leave it alone" — with one
    deliberate exception noted on ``clear_expiry``, because a nullable field
    cannot express "set this back to nothing" and "do not touch it" at once.
    """

    max_users: int | None = Field(default=None, ge=1, le=10_000)
    max_companies: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: UtcDatetime | None = None
    #: Removes the end date. Without it, an account given an expiry by mistake
    #: could never be made open-ended again.
    clear_expiry: bool = False
    partner_id: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


class StatusChangeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class PortalStats(BaseModel):
    """The counters across the top of the portal.

    ``pending`` is first because it is the only number anybody has to act on.
    """

    pending: int = 0
    active: int = 0
    suspended: int = 0
    rejected: int = 0
    expired: int = 0
    companies: int = 0
    connectors_online: int = 0


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


class LogLine(BaseModel):
    """One log line, from either source.

    Backend and connector lines share a shape so the portal renders them with
    one component and a support person reads them with one set of habits. The
    fields that only apply to one source are optional rather than split into two
    models -- the alternative is two log viewers that drift apart.
    """

    #: Position in the emitting process's stream. Zero for anything read back
    #: from the database, where it means nothing; the live tail resumes from it.
    seq: int = 0
    created_at: UtcDatetime
    level: str
    logger: str = ""
    message: str = ""
    #: Backend lines only: which instance, and the request id already stamped on
    #: every API response -- so a customer's screenshot is enough to find this.
    instance_id: str | None = None
    request_id: str | None = None
    traceback: str | None = None
    #: Connector lines only.
    connector_id: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    session_id: str | None = None
    #: The connector's own clock, which is not always right. Kept beside the
    #: receipt time precisely so a wrong clock is visible rather than confusing.
    logged_at: UtcDatetime | None = None
    #: Lines the connector's buffer discarded immediately before this one.
    dropped_before: int = 0


class LogPage(BaseModel):
    """A slice of log, newest first."""

    lines: list[LogLine] = Field(default_factory=list)
    #: Highest sequence number in this process's ring, so a client that polls
    #: instead of streaming can ask for "everything after" without duplicates.
    latest_seq: int = 0
    #: Whether these came from the in-memory ring (live, lost on restart) or the
    #: table (WARNING and above, survives one). The portal says which, because
    #: "there are no logs" means two very different things.
    source: str = "live"
    #: Which backend process answered. On a single-instance deploy this is
    #: noise; the moment there are two it is the first question worth asking.
    instance_id: str = ""


class ConnectorSummary(BaseModel):
    """One customer PC, as the support view lists it.

    Carries enough to choose between two of them without opening either: which
    machine, whether it is connected right now, whether Tally behind it is
    answering, and whether it has been logging errors.
    """

    id: str
    org_id: str
    org_name: str = ""
    label: str = ""
    hostname: str | None = None
    os: str | None = None
    connector_version: str | None = None
    status: str = ""
    online: bool = False
    tally_online: bool = False
    last_seen_at: UtcDatetime | None = None
    #: Newest log line we hold for this machine. ``None`` reads as "this
    #: connector has never shipped a log", which usually means a build too old
    #: to know how -- a different problem from "it has been quiet".
    last_log_at: UtcDatetime | None = None
    error_count: int = 0
    #: Stored lines held for this machine right now. The figure somebody needs
    #: before deciding whether clearing this one connector is worth doing.
    log_count: int = 0


class PurgeResult(BaseModel):
    """What a delete actually removed.

    The count is returned rather than a bare 204 because "clear this
    connector's logs" and "clear nothing, you were looking at a filtered view"
    are indistinguishable on screen otherwise, and the second is the one that
    sends somebody back to check whether the button works.
    """

    deleted: int = 0
    #: What was cleared, echoed back so a toast can say it without the caller
    #: reconstructing the sentence from the parameters it sent.
    scope: str = ""


class LogUsage(BaseModel):
    """How much the diagnostic tables are holding, and for how long.

    Retention is reported alongside the counts because a count on its own
    invites the wrong reaction. Eighty thousand connector lines is alarming
    until you know it is two days of the whole fleet and ages out by itself.
    """

    server_logs: int = 0
    connector_logs: int = 0
    audit_logs: int = 0
    server_retention_days: int = 0
    connector_retention_days: int = 0
    audit_retention_days: int = 0


class AuditEntry(BaseModel):
    """One recorded action, for the activity view."""

    id: str
    created_at: UtcDatetime
    action: str
    org_id: str | None = None
    org_name: str | None = None
    user_id: str | None = None
    actor: str | None = None
    company_id: str | None = None
    ip_address: str | None = None
    outcome: str = "ok"
    duration_ms: int = 0
    detail: dict | None = None
