"""Password hashing, token signing, and secret generation.

Kept free of database and FastAPI imports so it can be unit-tested and reused by
the pairing CLI without dragging in an app context.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Argon2id at library defaults: ~64 MiB and ~50ms per verify on server hardware.
#: Deliberately slow -- this is the only thing standing between a leaked database
#: and every customer's books.
_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash used weaker parameters than current policy.

    Lets us raise the cost over time and upgrade each user's hash silently on
    their next successful login.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_secret(nbytes: int = 32) -> str:
    """A connector pairing secret. Shown once, stored only as a hash."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Fast hash for high-entropy tokens.

    SHA-256, not Argon2, and that is correct: these are 256-bit random strings,
    so there is no dictionary to attack and no reason to pay a slow KDF on every
    refresh. Argon2 is for *user-chosen* passwords.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    token_type: TokenType
    expires_at: datetime
    org_id: str | None = None
    role: str | None = None
    jti: str | None = None
    raw: dict[str, Any] | None = None


class TokenError(Exception):
    """Signature invalid, expired, or the wrong token type."""


def create_token(
    *,
    subject: str,
    token_type: TokenType,
    secret: str,
    algorithm: str,
    ttl_seconds: int,
    org_id: str | None = None,
    role: str | None = None,
    jti: str | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if org_id:
        payload["org"] = org_id
    if role:
        payload["role"] = role
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(
    token: str,
    *,
    secret: str,
    algorithm: str,
    expected_type: TokenType | None = None,
) -> TokenClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token is invalid") from exc

    token_type = payload.get("typ")
    # Without this check a refresh token would be accepted as an access token,
    # silently turning a 30-day credential into an API key.
    if expected_type is not None and token_type != expected_type:
        raise TokenError(f"expected a {expected_type} token, got {token_type!r}")

    return TokenClaims(
        subject=payload["sub"],
        token_type=token_type,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        org_id=payload.get("org"),
        role=payload.get("role"),
        jti=payload.get("jti"),
        raw=payload,
    )
