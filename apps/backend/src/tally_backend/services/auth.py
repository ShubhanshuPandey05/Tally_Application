"""Registration, login, and refresh-token rotation."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..core.errors import AuthenticationError, ConflictError, PermissionDenied
from ..core.security import (
    create_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from ..db.models import Membership, Organisation, RefreshToken, Role, User, new_id, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # -- registration ----------------------------------------------------

    async def register(
        self, *, email: str, password: str, full_name: str | None, org_name: str
    ) -> tuple[User, Organisation]:
        email = email.strip().lower()
        existing = await self._session.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise ConflictError(
                f"user {email} already exists",
                user_message="An account with that email already exists.",
            )

        user = User(email=email, password_hash=hash_password(password), full_name=full_name)
        org = Organisation(name=org_name)
        # The person who creates the organisation owns it; without this the first
        # user could not pair a connector or invite anyone.
        membership = Membership(user=user, organisation=org, role=Role.ADMIN)

        self._session.add_all([user, org, membership])
        await self._session.flush()
        return user, org

    # -- login -----------------------------------------------------------

    async def authenticate(self, *, email: str, password: str) -> User:
        email = email.strip().lower()
        user = await self._session.scalar(select(User).where(User.email == email))

        if user is None:
            # Hash anyway so a missing account and a wrong password take the same
            # time. Otherwise response latency enumerates who has an account.
            hash_password(password)
            raise AuthenticationError(
                "no such user", user_message="Incorrect email or password."
            )

        if not verify_password(password, user.password_hash):
            raise AuthenticationError(
                "bad password", user_message="Incorrect email or password."
            )

        if not user.is_active:
            raise PermissionDenied(
                "user is disabled", user_message="This account has been disabled."
            )

        # Transparently upgrade the stored hash if policy has since hardened.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        user.last_login_at = utc_now()
        await self._session.flush()
        return user

    async def primary_membership(self, user: User) -> Membership:
        stmt = (
            select(Membership)
            .where(Membership.user_id == user.id)
            .options(selectinload(Membership.organisation))
            .order_by(Membership.created_at)
        )
        membership = (await self._session.execute(stmt)).scalars().first()
        if membership is None:
            raise PermissionDenied(
                "user has no organisation",
                user_message="Your account is not linked to a business yet.",
            )
        return membership

    # -- tokens ----------------------------------------------------------

    async def issue_tokens(
        self,
        user: User,
        membership: Membership,
        *,
        device_name: str | None = None,
        user_agent: str | None = None,
        family_id: str | None = None,
    ) -> TokenPair:
        access = create_token(
            subject=user.id,
            token_type="access",
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            ttl_seconds=self._settings.access_token_ttl_seconds,
            org_id=membership.org_id,
            role=str(membership.role),
        )

        # Opaque and random, not a JWT: a refresh token must be revocable, and
        # revoking a self-contained token means a blocklist the size of the
        # thing it replaced.
        refresh_plain = secrets.token_urlsafe(48)
        self._session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh_plain),
                family_id=family_id or new_id(),
                device_name=device_name,
                user_agent=(user_agent or "")[:400] or None,
                expires_at=utc_now()
                + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
            )
        )
        await self._session.flush()

        return TokenPair(
            access_token=access,
            refresh_token=refresh_plain,
            expires_in=self._settings.access_token_ttl_seconds,
        )

    async def rotate_refresh_token(
        self, refresh_plain: str, *, user_agent: str | None = None
    ) -> TokenPair:
        """Exchange a refresh token for a new pair, invalidating the old one.

        Rotation is single-use. If a token that was already spent comes back, the
        realistic explanation is that it was stolen -- the legitimate device and
        an attacker are both using the same one -- so the whole family is
        revoked and everyone re-authenticates. Losing a session is a far better
        outcome than an attacker silently refreshing forever.
        """
        token_hash = hash_token(refresh_plain)
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

        if stored is None:
            raise AuthenticationError("unknown refresh token")

        if not stored.is_usable:
            if stored.revoked_at is not None:
                logger.warning(
                    "refresh token replay detected for user %s; revoking family %s",
                    stored.user_id,
                    stored.family_id,
                )
                await self._revoke_family(stored.family_id)
                # Committed before raising, on purpose. The 401 below rolls the
                # request's transaction back, which would otherwise discard the
                # revocation and leave the leaked family fully usable -- a
                # security control that silently does nothing.
                await self._session.commit()
            raise AuthenticationError("refresh token is no longer valid")

        stored.revoked_at = utc_now()
        stored.last_used_at = utc_now()

        user = await self._session.get(User, stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("account is no longer active")

        membership = await self.primary_membership(user)
        return await self.issue_tokens(
            user,
            membership,
            device_name=stored.device_name,
            user_agent=user_agent or stored.user_agent,
            family_id=stored.family_id,  # stay in the same family across rotations
        )

    async def revoke(self, refresh_plain: str) -> None:
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_plain))
        )
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = utc_now()

    async def revoke_all(self, user_id: str) -> int:
        """Sign out every device. The "my phone was stolen" button."""
        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        tokens = (await self._session.execute(stmt)).scalars().all()
        for token in tokens:
            token.revoked_at = utc_now()
        return len(tokens)

    async def _revoke_family(self, family_id: str) -> None:
        stmt = select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
        for token in (await self._session.execute(stmt)).scalars().all():
            token.revoked_at = utc_now()
