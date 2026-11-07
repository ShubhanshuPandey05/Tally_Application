"""Proving a phone number with Firebase's SMS sign-in.

The app does the SMS half: it asks Firebase to text a code, the person types
it, and Firebase hands the app an ID token that says "this installation proved
+91 98765 43210 a moment ago". The app sends that token with the signup and
this module checks it. Firebase never becomes how anybody signs in to Prime
Analyst -- the token is used once, as evidence about a number, and our own
email-and-password session carries on exactly as before.

Why Firebase rather than texting a code ourselves: Indian SMS needs a DLT
registered sender and template before a single message is delivered, and
Firebase already holds both. It also does the parts that make a code safe --
expiry, attempt limits, abuse checks against the phone -- which a hand-rolled
sender would have to grow one incident at a time.

The token is a Google-signed JWT, verified here against Google's published
keys with no Firebase SDK and no service account: checking a signature needs
only the public half. See
https://firebase.google.com/docs/auth/admin/verify-id-tokens#verify_id_tokens_using_a_third-party_jwt_library
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Protocol

import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate

from ..core.errors import InvalidInput, NotConfigured

logger = logging.getLogger(__name__)

#: Google's signing certificates for Firebase ID tokens, keyed by ``kid``.
CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

#: How recently the SMS code must have been typed. An ID token lives an hour,
#: but the proof it carries is of the moment the code was entered; a signup
#: arriving long after that is somebody replaying a token, not finishing a form.
MAX_PROOF_AGE_SECONDS = 15 * 60

_MAX_AGE = re.compile(r"max-age=(\d+)")


class PhoneVerifier(Protocol):
    """Turns a Firebase ID token into the phone number it proves."""

    @property
    def enabled(self) -> bool: ...

    async def verified_phone(self, id_token: str) -> str: ...


def _refused(reason: str) -> InvalidInput:
    # One sentence for every way a token can be wrong. The person can act on
    # "do it again"; they cannot act on "audience mismatch".
    return InvalidInput(
        f"phone proof refused: {reason}",
        user_message=(
            "We could not confirm your mobile number. Ask for a new code and "
            "try again."
        ),
        detail={"field": "phone"},
    )


class FirebasePhoneVerifier:
    """Checks Firebase ID tokens against Google's published certificates."""

    def __init__(self, project_id: str | None, *, timeout_seconds: float = 10.0) -> None:
        self._project_id = project_id or None
        self._timeout = timeout_seconds
        self._keys: dict[str, object] = {}
        self._keys_expire_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._project_id is not None

    async def verified_phone(self, id_token: str) -> str:
        if self._project_id is None:
            raise NotConfigured("phone verification is not configured")

        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as error:
            raise _refused(f"unreadable token: {error}") from error
        if header.get("alg") != "RS256":
            raise _refused(f"algorithm {header.get('alg')!r}")

        key = await self._key_for(str(header.get("kid") or ""))
        if key is None:
            raise _refused("unknown signing key")

        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self._project_id,
                issuer=f"https://securetoken.google.com/{self._project_id}",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                # Phones' clocks are not ours to fix, and Google's are not
                # perfect either; a few seconds either way is not an attack.
                leeway=30,
            )
        except jwt.PyJWTError as error:
            raise _refused(str(error)) from error

        if not claims.get("sub"):
            raise _refused("no subject")
        firebase = claims.get("firebase") or {}
        if firebase.get("sign_in_provider") != "phone":
            # A Firebase token from an email or Google sign-in proves an
            # address, not a phone, even if a number happens to ride along.
            raise _refused(f"provider {firebase.get('sign_in_provider')!r}")

        auth_time = claims.get("auth_time")
        if not isinstance(auth_time, int | float):
            raise _refused("no auth_time")
        if time.time() - float(auth_time) > MAX_PROOF_AGE_SECONDS:
            raise _refused("proof too old")

        phone = claims.get("phone_number")
        if not isinstance(phone, str) or not phone.startswith("+"):
            raise _refused("no phone number")
        return phone

    async def _key_for(self, kid: str) -> object | None:
        if not kid:
            return None
        if kid in self._keys and time.monotonic() < self._keys_expire_at:
            return self._keys[kid]
        async with self._lock:
            # Google rotates these keys every few hours; an unknown kid is
            # the moment to look again rather than refuse a real token.
            if kid not in self._keys or time.monotonic() >= self._keys_expire_at:
                await self._refresh_keys()
        return self._keys.get(kid)

    async def _refresh_keys(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(CERTS_URL)
                response.raise_for_status()
                certs = response.json()
        except (httpx.HTTPError, ValueError) as error:
            # Keep whatever keys we had. A token signed by one of them still
            # verifies, and a brief Google outage must not stop every signup.
            logger.warning("could not refresh Firebase signing keys: %s", error)
            raise NotConfigured(
                "Firebase signing keys unavailable",
                user_message=(
                    "We could not confirm your mobile number just now. "
                    "Please try again in a minute."
                ),
            ) from error

        keys: dict[str, object] = {}
        for kid, pem in certs.items():
            try:
                keys[kid] = load_pem_x509_certificate(pem.encode()).public_key()
            except ValueError:
                logger.warning("skipping unreadable Firebase certificate %s", kid)
        match = _MAX_AGE.search(response.headers.get("cache-control", ""))
        max_age = int(match.group(1)) if match else 3600
        self._keys = keys
        self._keys_expire_at = time.monotonic() + max_age
