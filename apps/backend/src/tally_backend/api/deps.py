"""Request-scoped dependencies.

The important one is :func:`get_company`. Every data endpoint takes a company id
from the URL, and it is the *only* place tenant isolation is enforced -- so it is
written to fail closed: a company whose ``org_id`` does not match the caller's
returns 404, not 403. Saying "forbidden" would confirm the id exists, which lets
one customer probe for another's company ids.
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
from ..db.models import Company, Connector, Membership, Role, User
from ..hub import ConnectorHub
from ..services.auth import AuthService
from ..services.dashboard import DashboardService
from ..services.reads import ReadService


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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
HubDep = Annotated[ConnectorHub, Depends(get_hub)]


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and the organisation they are acting in."""

    user: User
    org_id: str
    role: Role

    def require(self, minimum: Role) -> None:
        if not self.role.allows(minimum):
            raise PermissionDenied(
                f"role {self.role} < {minimum}",
                user_message="You do not have permission to do that.",
            )


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

    return Principal(user=user, org_id=membership.org_id, role=membership.role)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_company(
    company_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> Company:
    company = await session.get(Company, company_id)
    # 404 for both "missing" and "not yours": a 403 here would confirm the id
    # belongs to somebody, which is an enumeration oracle.
    if company is None or company.org_id != principal.org_id:
        raise NotFound(f"company {company_id}", user_message="That company was not found.")
    if not company.is_active:
        raise NotFound("company is inactive", user_message="That company is no longer active.")
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
