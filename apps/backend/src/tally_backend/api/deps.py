"""Request-scoped dependencies.

The important one is :func:`get_company`. Every data endpoint takes a company id
from the URL, and it is the *only* place tenant isolation is enforced -- so it is
written to fail closed: a company whose ``org_id`` does not match the caller's
returns 404, not 403. Saying "forbidden" would confirm the id exists, which lets
one customer probe for another's company ids.

It now enforces three boundaries rather than one. Between organisations, nothing
is shared. *Within* an organisation, an admin sees every company and a staff
member sees only the ones granted to them in ``company_access``. Both answer 404
for the same reason: the reply must not reveal that the company exists. And
before either, the organisation's own subscription has to be live — a suspended
account is refused with 402 whether the company id is real or invented.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core.errors import AuthenticationError, NotFound, PermissionDenied
from ..core.security import TokenError, decode_token
from ..db.models import (
    Company,
    CompanyAccess,
    Connector,
    Membership,
    Organisation,
    PlatformRole,
    PlatformUser,
    Role,
    User,
)
from ..hub import ConnectorHub
from ..services.auth import AuthService
from ..services.dashboard import DashboardService
from ..services.entitlements import Entitlement, require_changes, require_data
from ..services.public_stats import PublicStatsService
from ..services.reads import ReadService
from ..services.sync import SyncCoordinator


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_hub(request: Request) -> ConnectorHub:
    return request.app.state.hub


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed on success."""
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_public_stats(request: Request) -> PublicStatsService:
    return request.app.state.public_stats


def get_sync(request: Request) -> SyncCoordinator:
    return request.app.state.sync


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
HubDep = Annotated[ConnectorHub, Depends(get_hub)]
SyncDep = Annotated[SyncCoordinator, Depends(get_sync)]
PublicStatsDep = Annotated[PublicStatsService, Depends(get_public_stats)]


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and the organisation they are acting in.

    Carries the whole :class:`Organisation` rather than just its id, because
    every request now has to answer two questions about it -- *who is this?* and
    *is this account live?* -- and fetching the row a second time inside each
    handler is how one of them ends up skipped.
    """

    user: User
    org: Organisation
    role: Role

    @property
    def org_id(self) -> str:
        return self.org.id

    @property
    def entitlement(self) -> Entitlement:
        return Entitlement.of(self.org)

    def require(self, minimum: Role) -> None:
        if not self.role.allows(minimum):
            raise PermissionDenied(
                f"role {self.role} < {minimum}",
                user_message="You do not have permission to do that.",
            )

    def require_changes(self) -> None:
        """The subscription gate on anything that makes the account bigger.

        Separate from :meth:`require` on purpose: being an admin of an account
        nobody has approved is a real and expected state -- it is what every
        customer is for the first few minutes -- and the two refusals need
        completely different words.
        """
        require_changes(self.org)


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")

    try:
        claims = decode_token(
            authorization.split(" ", 1)[1].strip(),
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expected_type="access",
        )
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = await session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("account is not active")

    # The role is re-read rather than trusted from the token. An access token
    # lives 15 minutes, and a membership downgraded during that window must take
    # effect immediately -- especially when it is a revoked accountant.
    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.org_id == claims.org_id
        )
    )
    if membership is None:
        raise PermissionDenied("no membership in that organisation")

    org = await session.get(Organisation, membership.org_id)
    if org is None:
        # A membership pointing at a deleted organisation. Refuse rather than
        # carry a null through -- there is no tenant to scope the request to.
        raise PermissionDenied("organisation no longer exists")

    return Principal(user=user, org=org, role=membership.role)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def visible_company_ids(session: AsyncSession, principal: Principal) -> set[str] | None:
    """Which companies this caller may see, or ``None`` for "all of them".

    ``None`` rather than a set of every id: an admin's access is defined by their
    role, not by rows, and materialising it as a set would invite a caller to
    treat the two the same way and quietly drop an admin's access the moment the
    org has more companies than the query happens to return.
    """
    if principal.role.allows(Role.ADMIN):
        return None
    rows = await session.execute(
        select(CompanyAccess.company_id).where(CompanyAccess.user_id == principal.user.id)
    )
    return set(rows.scalars().all())


async def get_company(
    company_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> Company:
    # Before the lookup, so a suspended account is refused identically whether
    # the company id is real or invented -- and so every report, export and sync
    # route inherits the subscription check from the one dependency they all
    # already pass through, rather than from whoever remembers to add it.
    require_data(principal.org)

    company = await session.get(Company, company_id)
    # 404 for both "missing" and "not yours": a 403 here would confirm the id
    # belongs to somebody, which is an enumeration oracle.
    if company is None or company.org_id != principal.org_id:
        raise NotFound(f"company {company_id}", user_message="That company was not found.")
    if not company.is_active:
        raise NotFound("company is inactive", user_message="That company is no longer active.")

    # Every company-scoped route resolves through here, which is the only reason
    # per-company access is enforceable in one place instead of in each handler.
    # A new report added later is guarded by construction rather than by whoever
    # remembers -- which is the same reason the connector's loaded-company guard
    # sits in front of every read rather than in each query.
    #
    # 404 again, not 403: a staff member probing ids must not be able to learn
    # which companies exist in the org that they were not given.
    visible = await visible_company_ids(session, principal)
    if visible is not None and company.id not in visible:
        raise NotFound(
            f"company {company_id} not granted to user {principal.user.id}",
            user_message="That company was not found.",
        )
    return company


CompanyDep = Annotated[Company, Depends(get_company)]


async def get_connector(
    connector_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> Connector:
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != principal.org_id:
        raise NotFound(f"connector {connector_id}", user_message="That connector was not found.")
    return connector


ConnectorDep = Annotated[Connector, Depends(get_connector)]


# --------------------------------------------------------------------------
# The management portal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformPrincipal:
    """Whoever is signed in to the management portal.

    Structurally similar to :class:`Principal` and deliberately not related to
    it. Nothing accepts both, so there is no call site where a customer's
    session could be mistaken for a partner's.
    """

    user: PlatformUser

    @property
    def role(self) -> PlatformRole:
        return self.user.role

    def require(self, minimum: PlatformRole) -> None:
        if not self.role.allows(minimum):
            raise PermissionDenied(
                f"platform role {self.role} < {minimum}",
                user_message="Only a TallyFlow owner can do that.",
            )

    @property
    def partner_scope(self) -> str | None:
        """Whose accounts this person may see -- ``None`` meaning everyone's.

        ``None`` rather than a materialised list of ids, for the same reason
        :func:`visible_company_ids` returns ``None`` for an admin: an owner's
        reach is defined by their role, and turning it into a set invites a
        caller to treat "all" as "the ones I happened to fetch".
        """
        return None if self.role.allows(PlatformRole.OWNER) else self.user.id


async def get_platform_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> PlatformPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")

    try:
        claims = decode_token(
            authorization.split(" ", 1)[1].strip(),
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            # The one line that keeps a customer's access token from reaching
            # the portal. `typ` is signed, so it cannot be edited in transit.
            expected_type="portal",
        )
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = await session.get(PlatformUser, claims.subject)
    # Re-read rather than trusted from the token, so switching a partner off
    # ends their session now instead of in up to eight hours.
    if user is None or not user.is_active:
        raise AuthenticationError("portal account is not active")
    return PlatformPrincipal(user=user)


PlatformPrincipalDep = Annotated[PlatformPrincipal, Depends(get_platform_principal)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


def get_read_service(session: SessionDep, hub: HubDep, settings: SettingsDep) -> ReadService:
    return ReadService(session, hub, settings)


def get_dashboard_service(
    reads: Annotated[ReadService, Depends(get_read_service)],
) -> DashboardService:
    return DashboardService(reads)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ReadServiceDep = Annotated[ReadService, Depends(get_read_service)]
DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]
