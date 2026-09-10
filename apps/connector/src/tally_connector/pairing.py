"""Getting paired without anybody typing a secret.

The shop owner's phone already holds their account. The Windows PC holds Tally.
Everything that made the old flow painful came from the credential having to
travel between those two by hand: a connector id and a 43-character secret, read
off a phone screen, typed into a keyboard across the room, in a shop.

So the credential travels over TLS instead, and the *code* travels by camera:

1. This module asks the backend to open a claim and gets back two strings.
2. The connector's local page draws the first one as a QR code.
3. The owner scans it in the app and says which account this PC belongs to.
4. This module collects the pairing with the second string -- the one that was
   never on screen -- and writes it to ``connector.json``.

The connector dials out at every step. Nothing here listens, and no port is
opened on the customer's network: the phone never talks to this machine at all,
it talks to the backend, which is the same shape as every other thing the
connector does.

Polling rather than a socket, because the connector has no credential yet and
the socket endpoint is the one thing that requires one. It is a handful of
requests over a couple of minutes while somebody walks across a shop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

#: Gap between collection attempts. Short enough that the page says "paired"
#: while the owner is still looking at their phone, long enough that a machine
#: left on the pairing screen overnight is not a request every second.
POLL_INTERVAL_SECONDS = 3.0

#: A claim the backend never answers is not a reason to hammer it. After this
#: the connector asks for a fresh code, which is also what the page's own
#: expiry countdown is telling the person in front of it to expect.
CLAIM_LIFETIME_SECONDS = 15 * 60

#: The QR payload's shape. Bumped only if the app must be able to tell an old
#: code from a new one -- an app that cannot parse a code has to say so, and it
#: cannot say so if the code is a bare string with nothing to check.
PAYLOAD_VERSION = 1


class PairingError(RuntimeError):
    """The backend refused, or could not be reached, during pairing."""


@dataclass(frozen=True)
class PendingClaim:
    """A code on screen, and the token that will collect against it."""

    code: str
    token: str
    expires_at: datetime
    #: The host the code was opened against. Carried into the QR so the app can
    #: say "that PC is pointed at a different TallyFlow server" instead of
    #: failing with a code that looks perfectly valid.
    api_host: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now(UTC)

    @property
    def seconds_left(self) -> int:
        return max(0, int((self.expires_at - datetime.now(UTC)).total_seconds()))

    def payload(self) -> str:
        """What the QR code actually contains.

        Compact deliberately. Every character is another module in the symbol,
        and the symbol has to survive being read by a cheap phone camera, at an
        angle, off a monitor that may be dusty. Single-letter keys keep this
        near 90 characters, which is a QR small enough to scan from a step back.
        """
        return json.dumps(
            {"v": PAYLOAD_VERSION, "c": self.code, "h": self.api_host},
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class Pairing:
    """Credentials collected from a claim, ready to be written to disk."""

    connector_id: str
    secret: str
    name: str


class PairingClient:
    """Talks to the backend's unauthenticated pairing routes."""

    def __init__(
        self,
        *,
        api_base_url: str,
        verify_tls: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = api_base_url.rstrip("/")
        self._verify_tls = verify_tls
        self._client = client
        self._owns_client = client is None

    @property
    def api_host(self) -> str:
        return httpx.URL(self._base).host

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self._verify_tls, follow_redirects=True, timeout=20.0
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def open_claim(
        self, *, hostname: str, os_name: str, connector_version: str
    ) -> PendingClaim:
        """Ask for a code to show."""
        try:
            response = await self._ensure_client().post(
                f"{self._base}/v1/pairing/claims",
                json={
                    "hostname": hostname,
                    "os": os_name,
                    "connector_version": connector_version,
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise PairingError(f"could not reach {self._base}: {exc}") from exc
        except ValueError as exc:
            raise PairingError("the server's pairing reply was not JSON") from exc

        code, token = body.get("code"), body.get("token")
        if not code or not token:
            raise PairingError("the server did not issue a pairing code")

        # The server's own lifetime, floored to something usable: a backend that
        # answered zero would otherwise put the page into a loop of expiring
        # codes that nobody could photograph in time.
        lifetime = max(60, int(body.get("expires_in_seconds") or CLAIM_LIFETIME_SECONDS))
        return PendingClaim(
            code=code,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(seconds=lifetime),
            api_host=self.api_host,
        )

    async def collect(self, claim: PendingClaim) -> Pairing | None:
        """Pick up the credentials, or ``None`` while nobody has scanned yet.

        Errors are raised rather than swallowed so the page can say *why* it is
        not pairing. "Waiting to be scanned" and "that server is unreachable"
        look identical from a spinner, and they need opposite things done about
        them.
        """
        try:
            response = await self._ensure_client().post(
                f"{self._base}/v1/pairing/claims/collect",
                json={"code": claim.code, "token": claim.token},
            )
        except httpx.HTTPError as exc:
            raise PairingError(f"could not reach {self._base}: {exc}") from exc

        if response.status_code == 404:
            # The claim expired, or was never there. A fresh code is the only
            # way forward, and the caller opens one.
            raise PairingError("this pairing code has expired")
        if response.status_code >= 400:
            raise PairingError(_message_from(response))

        try:
            body = response.json()
        except ValueError as exc:
            raise PairingError("the server's pairing reply was not JSON") from exc

        if body.get("status") != "ready":
            return None

        connector_id, secret = body.get("connector_id"), body.get("secret")
        if not connector_id or not secret:
            raise PairingError("the server returned an incomplete pairing")
        return Pairing(
            connector_id=connector_id, secret=secret, name=body.get("name") or "Tally PC"
        )

    async def wait_for_pairing(
        self, claim: PendingClaim, *, stop: asyncio.Event
    ) -> Pairing | None:
        """Poll until the claim is collected, expires, or ``stop`` is set.

        ``None`` means "ask for a new code": either the claim ran out or the
        connector is shutting down. A transient error does not end the wait --
        an internet blip while somebody is walking across a shop must not
        invalidate the code they are about to scan.
        """
        while not stop.is_set() and not claim.is_expired:
            try:
                pairing = await self.collect(claim)
            except PairingError as exc:
                message = str(exc)
                if "expired" in message:
                    return None
                logger.info("still waiting to be paired: %s", message)
                pairing = None
            if pairing is not None:
                return pairing
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                continue
        return None


def _message_from(response: httpx.Response) -> str:
    """The backend's own wording for a refusal, when it gave one."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"the server refused pairing ({response.status_code})"
    return error.get("message") or f"the server refused pairing ({response.status_code})"
