"""Forgotten passwords: a six-digit code sent to the account's email.

Until this existed, an admin who forgot their own password had no way back in
-- an admin can reissue a colleague's password, but nobody can reissue the
admin's. Email rather than SMS because the address is already the account's
identity, and because an SMS costs money per message while an email does not.

The rules, and the failure each one prevents:

* **The answer never says whether an address has an account.** Asking for a
  code always looks the same, and so does a wrong code against an address with
  no account. Otherwise this form is a free "who uses Prime Analyst?" lookup.
* **The code is stored keyed-hashed** -- see ``PasswordResetCode``.
* **One live code, a handful of guesses, a short life.** Asking again spends
  the last code; too many wrong guesses spend this one.
* **Setting the password signs out every device.** Someone resetting a
  password may be doing it *because* somebody else got in.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core.errors import InvalidInput
from ..core.security import hash_password
from ..db.models import PasswordResetCode, RefreshToken, User, as_utc, utc_now

#: Codes per account per hour, however they are spaced. With the resend gap
#: this bounds what one account's inbox can be sent.
MAX_CODES_PER_HOUR = 5


@dataclass(frozen=True)
class CodeToSend:
    user_id: str
    email: str
    code: str
    full_name: str | None


def _digest(settings: Settings, row_id: str, code: str) -> str:
    # The row id is mixed in so two accounts sent the same six digits do not
    # store the same digest, which would let one row be matched to another.
    return hmac.new(
        settings.jwt_secret.encode(), f"{row_id}|{code}".encode(), hashlib.sha256
    ).hexdigest()


def _no_code() -> InvalidInput:
    return InvalidInput(
        "no usable reset code",
        user_message="That code has expired or was already used. Ask for a new one.",
        detail={"field": "code"},
    )


class PasswordResetService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def issue(self, email: str) -> CodeToSend | None:
        """A new code for ``email``, or ``None`` when nothing should be sent.

        ``None`` covers an address with no account, a disabled account, and
        an account asking too often -- and the caller answers all of them
        exactly as it answers a code that was sent.
        """
        user = await self._session.scalar(
            select(User).where(User.email == email.strip().lower())
        )
        if user is None or not user.is_active:
            return None

        now = utc_now()
        latest = await self._session.scalar(
            select(func.max(PasswordResetCode.created_at)).where(
                PasswordResetCode.user_id == user.id
            )
        )
        if latest is not None and (now - as_utc(latest)).total_seconds() < (
            self._settings.password_reset_resend_seconds
        ):
            return None
        recent = await self._session.scalar(
            select(func.count())
            .select_from(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.created_at > now - timedelta(hours=1),
            )
        )
        if (recent or 0) >= MAX_CODES_PER_HOUR:
            return None

        await self._spend_live_codes(user.id)
        code = f"{secrets.randbelow(1_000_000):06d}"
        row = PasswordResetCode(
            user_id=user.id,
            code_hash="",
            attempts=0,
            created_at=now,
            expires_at=now + timedelta(minutes=self._settings.password_reset_code_minutes),
        )
        self._session.add(row)
        await self._session.flush()
        row.code_hash = _digest(self._settings, row.id, code)
        await self._session.flush()
        return CodeToSend(user_id=user.id, email=user.email, code=code, full_name=user.full_name)

    async def reset(self, *, email: str, code: str, new_password: str) -> User:
        """Set a new password with a code. Refuses with a sentence otherwise."""
        user = await self._session.scalar(
            select(User).where(User.email == email.strip().lower())
        )
        if user is None or not user.is_active:
            raise _no_code()

        row = await self._session.scalar(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.spent_at.is_(None),
            )
            .order_by(PasswordResetCode.created_at.desc())
            .limit(1)
        )
        now = utc_now()
        if row is None or as_utc(row.expires_at) <= now:
            raise _no_code()

        expected = row.code_hash
        if not hmac.compare_digest(expected, _digest(self._settings, row.id, code.strip())):
            row.attempts += 1
            left = self._settings.password_reset_max_attempts - row.attempts
            if left <= 0:
                row.spent_at = now
            # Committed before refusing. The request's session rolls back on
            # any error, and a guess counter that rolls back with it counts
            # nothing -- six digits would then be a million free guesses.
            await self._session.commit()
            if left <= 0:
                raise InvalidInput(
                    "reset code burned",
                    user_message="Too many wrong codes. Ask for a new one.",
                    detail={"field": "code"},
                )
            raise InvalidInput(
                "wrong reset code",
                user_message="That code is not right. Check the email and try again.",
                detail={"field": "code"},
            )

        row.spent_at = now
        user.password_hash = hash_password(new_password)
        # The person chose this one themselves, which is what the flag was
        # waiting for on an admin-issued password.
        user.must_change_password = False
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self._session.flush()
        return user

    async def _spend_live_codes(self, user_id: str) -> None:
        await self._session.execute(
            update(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user_id,
                PasswordResetCode.spent_at.is_(None),
            )
            .values(spent_at=utc_now())
        )


def reset_email(to_send: CodeToSend, minutes: int) -> tuple[str, str]:
    """Subject and body. Plain text: it renders everywhere and looks like what
    it is, which is the first thing a person checks before trusting a code."""
    greeting = f"Hello {to_send.full_name}," if to_send.full_name else "Hello,"
    body = (
        f"{greeting}\n\n"
        f"Your Prime Analyst password reset code is:\n\n"
        f"    {to_send.code}\n\n"
        f"Type it into the app within {minutes} minutes. It works once.\n\n"
        "If you did not ask to reset your password, you can ignore this email. "
        "Your password has not changed, and nobody can change it without this code.\n\n"
        "-- Prime Analyst"
    )
    return f"{to_send.code} is your Prime Analyst reset code", body
