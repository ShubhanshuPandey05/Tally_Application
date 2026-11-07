"""Push notifications, through Firebase Cloud Messaging.

Phones register an FCM token after sign-in (``api/v1/push.py``); this module
sends to them. Sending is free and needs a Firebase service account, but no
Firebase SDK: FCM's HTTP v1 API takes an OAuth token we mint ourselves from
the account's key.

**What a notification may say.** It travels through Google's servers, and on
iPhones through Apple's, and it is shown on a lock screen to whoever is
holding the phone. So it says *that* something happened, never what is in the
books: no amounts, no party names, no figures. Customer books are sealed at
rest so that nobody outside the request can read them; a lock-screen banner
must not become the exception. The person taps through to see the detail, in
an app that checks who they are.

**It cannot hurt what triggered it.** :meth:`PushService.notify_user` never
raises and is always awaited after the work it reports on has committed --
a notification about a receipt reaching Tally must never be the reason the
receipt did not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from sqlalchemy import delete, select

from ..config import Settings
from ..db.models import PushDevice

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    #: The device's token is no longer valid -- the app was uninstalled, or
    #: the token was rotated. Its row is removed.
    gone: bool = False


#: Sends one message to one token. Swappable so tests can see what was sent.
Transport = Callable[[str, dict[str, Any]], Awaitable[SendResult]]


def load_service_account(value: str | None) -> dict[str, Any] | None:
    """The key as a dict, from the JSON itself or from a path to it.

    Either form, because a container takes a secret as an environment value
    and a VM takes it as a file, and both are reasonable places to keep it.
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    if not text.startswith("{"):
        text = Path(text).read_text(encoding="utf-8")
    account = json.loads(text)
    for field in ("client_email", "private_key", "project_id"):
        if not account.get(field):
            raise ValueError(f"FCM service account is missing {field}")
    return account


class PushService:
    def __init__(self, session_factory: SessionFactory, settings: Settings) -> None:
        self._session_factory = session_factory
        try:
            self._account = load_service_account(settings.fcm_service_account)
        except (OSError, ValueError) as error:
            # Logged, not raised: a bad push key must not stop the API from
            # starting. Notifications are off until it is fixed.
            logger.error("push notifications disabled: %s", error)
            self._account = None
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._lock = asyncio.Lock()
        self.transport: Transport = self._fcm_send

    @property
    def enabled(self) -> bool:
        return self._account is not None

    async def notify_user(
        self,
        user_id: str | None,
        *,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> int:
        """Send to every phone this person is signed in on. Returns how many
        accepted it. Never raises -- see the module docstring."""
        if user_id is None or not self.enabled:
            return 0
        try:
            async with self._session_factory() as session:
                tokens = list(
                    (
                        await session.scalars(
                            select(PushDevice.token).where(PushDevice.user_id == user_id)
                        )
                    ).all()
                )
            if not tokens:
                return 0

            message = {
                "notification": {"title": title, "body": body},
                # FCM data values must be strings; the app routes on these.
                "data": {k: str(v) for k, v in (data or {}).items()},
                "android": {"priority": "high"},
            }
            sent = 0
            gone: list[str] = []
            for token in tokens:
                result = await self.transport(token, message)
                if result.ok:
                    sent += 1
                elif result.gone:
                    gone.append(token)

            if gone:
                async with self._session_factory() as session:
                    await session.execute(delete(PushDevice).where(PushDevice.token.in_(gone)))
                    await session.commit()
            return sent
        except Exception:
            logger.exception("push notification to %s failed", user_id)
            return 0

    # -- FCM HTTP v1 -----------------------------------------------------

    async def _fcm_send(self, token: str, message: dict[str, Any]) -> SendResult:
        assert self._account is not None
        access = await self._bearer()
        url = (
            "https://fcm.googleapis.com/v1/projects/"
            f"{self._account['project_id']}/messages:send"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access}"},
                json={"message": {**message, "token": token}},
            )
        if response.status_code == 200:
            return SendResult(ok=True)
        # 404 UNREGISTERED is the documented answer for an uninstalled app;
        # 400 on the token field is a token that was never valid.
        text = response.text
        gone = response.status_code == 404 or (
            response.status_code == 400 and "registration token" in text.lower()
        )
        if not gone:
            logger.warning("FCM refused a message (%s): %s", response.status_code, text[:300])
        return SendResult(ok=False, gone=gone)

    async def _bearer(self) -> str:
        if self._access_token and time.time() < self._access_expires_at:
            return self._access_token
        async with self._lock:
            if self._access_token and time.time() < self._access_expires_at:
                return self._access_token
            assert self._account is not None
            token_uri = self._account.get("token_uri") or _DEFAULT_TOKEN_URI
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": self._account["client_email"],
                    "scope": _SCOPE,
                    "aud": token_uri,
                    "iat": now,
                    "exp": now + 3600,
                },
                self._account["private_key"],
                algorithm="RS256",
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    token_uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )
                response.raise_for_status()
                body = response.json()
            self._access_token = body["access_token"]
            # A minute early, so a token is never presented in its last second.
            self._access_expires_at = time.time() + int(body.get("expires_in", 3600)) - 60
            return self._access_token
