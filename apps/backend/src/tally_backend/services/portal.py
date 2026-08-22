"""Sign-in for the management portal, and the one way an operator first exists.

Portal accounts are never self-service. There is no "register" here and there
must not be one: the whole point of the approval flow is that somebody outside a
customer's organisation decides what that organisation gets, and an endpoint
that mints those deciders on demand would make the decision worthless.

That leaves a chicken-and-egg problem for a fresh deployment, which
:func:`bootstrap_owner` solves: one owner account seeded from the environment at
startup, flagged ``must_change_password`` because an environment variable lives
in a deployment manifest and a deployment manifest is not a password vault.
Seeding is idempotent and never touches an account that already exists — a
restart must not silently reset the password of a live portal account back to
whatever the manifest still says.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..core.errors import AuthenticationError, PermissionDenied
from ..core.security import (
    create_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from ..db.models import PlatformRole, PlatformUser, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortalSession:
    access_token: str
    expires_in: int
    token_type: str = "Bearer"


class PortalAuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def authenticate(self, *, email: str, password: str) -> PlatformUser:
        email = email.strip().lower()
        user = await self._session.scalar(
            select(PlatformUser).where(PlatformUser.email == email)
        )

        if user is None:
            # Hash anyway, so a missing account and a wrong password cost the
            # same. Otherwise response latency enumerates who has portal access,
            # which is a shorter list and a far more interesting one than the
            # customer side's.
            hash_password(password)
            raise AuthenticationError(
                "no such portal user", user_message="Incorrect email or password."
            )

        if not verify_password(password, user.password_hash):
            raise AuthenticationError(
                "bad portal password", user_message="Incorrect email or password."
            )

        if not user.is_active:
            raise PermissionDenied(
                "portal user is disabled",
                user_message="This portal account has been disabled.",
            )

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        user.last_login_at = utc_now()
        await self._session.flush()
        return user

    def issue_session(self, user: PlatformUser) -> PortalSession:
        """One self-contained token, no refresh family.

        See ``Settings.portal_token_ttl_seconds`` for why. The token is only a
        claim of identity: the row behind it is re-read on every request, so
        deactivating an account ends its session immediately rather than at
        expiry.
        """
        ttl = self._settings.portal_token_ttl_seconds
        return PortalSession(
            access_token=create_token(
                subject=user.id,
                token_type="portal",
                secret=self._settings.jwt_secret,
                algorithm=self._settings.jwt_algorithm,
                ttl_seconds=ttl,
                role=str(user.role),
            ),
            expires_in=ttl,
        )


async def bootstrap_owner(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Seed the first portal owner from the environment, once.

    Silent when unconfigured, because most environments should not have one:
    a second backend instance, a test run, a developer's laptop. Only the
    deployment that actually runs the portal needs to set the pair.
    """
    email = settings.portal_bootstrap_email.strip().lower()
    password = settings.portal_bootstrap_password
    if not email or not password:
        return

    async with session_factory() as session:
        existing = await session.scalar(
            select(PlatformUser).where(PlatformUser.email == email)
        )
        if existing is not None:
            # Deliberately not "reset it back to the manifest value". A restart
            # after somebody changed their password must not hand the old one
            # back, and a manifest that still holds a rotated credential is
            # exactly the situation where that would happen.
            logger.info("portal owner %s already exists; leaving it alone", email)
            return

        # Only ever creates the *first* account. Past that, portal accounts come
        # from an owner inside the portal, where the action is audited and
        # attributable to a person rather than to a container's environment.
        already_populated = await session.scalar(
            select(func.count()).select_from(PlatformUser)
        )
        if already_populated:
            logger.warning(
                "portal bootstrap skipped: %s portal accounts already exist, "
                "and %s is not one of them",
                already_populated,
                email,
            )
            return

        session.add(
            PlatformUser(
                email=email,
                password_hash=hash_password(password),
                full_name="TallyFlow Owner",
                role=PlatformRole.OWNER,
                must_change_password=True,
            )
        )
        await session.commit()
        logger.warning(
            "seeded portal owner %s from the environment -- sign in and change "
            "the password, it is still readable in the deployment config",
            email,
        )
