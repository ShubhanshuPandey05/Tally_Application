"""The backend <-> connector wire contract.

Transport is a single outbound WebSocket held open by the connector. Outbound
matters: the customer's Tally machine never accepts an inbound connection, so
there is no port to forward and nothing to expose to the internet.

Messages are JSON objects discriminated on ``type``. Every message carries
``protocol_version``; a connector that receives a version it does not understand
refuses the session rather than guessing, because silently misparsing a job
request is how a read-only product grows an accidental write path.

Adding write support later means adding new ``type`` values here. Existing
readers ignore unknown types by design, so old connectors degrade rather than
crash.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

#: Payloads above this many bytes are gzipped before going over the wire. A full
#: month's day book is comfortably megabytes of JSON, and connectors commonly sit
#: behind asymmetric home broadband where upload is the bottleneck.
COMPRESSION_THRESHOLD_BYTES = 8 * 1024


def utc_now() -> datetime:
    return datetime.now(UTC)


class Message(BaseModel):
    """Base for every wire message."""

    model_config = ConfigDict(extra="ignore")

    protocol_version: int = PROTOCOL_VERSION
    sent_at: datetime = Field(default_factory=utc_now)


class ClientMessage(Message):
    """Base for frames the connector sends.

    ``connector_version`` is stamped on *every* frame, not just the handshake,
    and the reason is the failure it removes rather than the bytes it costs. A
    version known only at ``hello`` time is a version the backend learns once
    per reconnect -- so a connector that stays connected for a week is a
    connector the backend cannot re-evaluate for a week, which is exactly how a
    fleet drifts months apart in version. Stamped per frame, the backend
    re-checks on every heartbeat and the answer is never stale.

    The cost is ~30 bytes on a frame whose payload is routinely megabytes.
    """

    connector_version: str = ""


class ServerMessage(Message):
    """Base for frames the backend sends.

    Carries the release floor in the other direction, on the same reasoning:
    the connector learns what it *should* be running from ordinary traffic --
    every ping, every job -- instead of waiting out a poll interval. A backend
    deploy therefore reaches the whole fleet within one heartbeat.

    Both fields default to empty, which means "the backend did not say". An
    empty value must always read as "no opinion, carry on" rather than as
    version ``0`` -- a backend with no release manifest configured would
    otherwise look like it was ordering every connector to downgrade.
    """

    #: Newest published connector build. Empty when the backend does not know.
    latest_connector_version: str = ""
    #: Below this a connector can no longer be served correctly. Empty = no floor.
    min_connector_version: str = ""


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


class QueryCapability(BaseModel):
    name: str
    version: int
    heavy: bool = False


class HostInfo(BaseModel):
    """Identifies the machine, for the "which PC is this?" screen in the app."""

    hostname: str
    os: str
    connector_version: str
    python_version: str


class Hello(ClientMessage):
    """First frame the connector sends after the socket opens.

    Authentication is an HMAC over ``connector_id|nonce|issued_at`` using the
    shared secret rather than the secret itself. The secret therefore never
    crosses the wire, and ``nonce`` plus ``issued_at`` let the backend reject
    replays without keeping per-connector session state.
    """

    type: Literal["hello"] = "hello"
    connector_id: str
    nonce: str
    issued_at: int
    signature: str
    host: HostInfo
    capabilities: list[QueryCapability]

    @classmethod
    def signed(
        cls,
        *,
        connector_id: str,
        secret: str,
        host: HostInfo,
        capabilities: list[QueryCapability],
    ) -> Hello:
        nonce = secrets.token_hex(16)
        issued_at = int(time.time())
        return cls(
            connector_id=connector_id,
            nonce=nonce,
            issued_at=issued_at,
            signature=sign_handshake(
                connector_id=connector_id, nonce=nonce, issued_at=issued_at, secret=secret
            ),
            host=host,
            capabilities=capabilities,
        )


def sign_handshake(*, connector_id: str, nonce: str, issued_at: int, secret: str) -> str:
    """HMAC-SHA256 over the handshake triple.

    The backend recomputes this with its stored copy of the secret. Kept as a
    module-level function so the backend can import and verify with the exact
    same construction -- a mismatched canonical string is a classic source of
    "works locally, fails in production" auth bugs.
    """
    canonical = f"{connector_id}|{nonce}|{issued_at}".encode()
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def verify_handshake(hello: Hello, *, secret: str, max_age_seconds: int = 120) -> bool:
    """Constant-time signature check plus a freshness window."""
    if abs(int(time.time()) - hello.issued_at) > max_age_seconds:
        return False
    expected = sign_handshake(
        connector_id=hello.connector_id,
        nonce=hello.nonce,
        issued_at=hello.issued_at,
        secret=secret,
    )
    return hmac.compare_digest(expected, hello.signature)


class HelloAck(ServerMessage):
    """Backend's answer to :class:`Hello`."""

    type: Literal["hello_ack"] = "hello_ack"
    accepted: bool
    session_id: str | None = None
    heartbeat_interval_seconds: float = 30.0
    reason: str | None = None


# --------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------


class Ping(ServerMessage):
    type: Literal["ping"] = "ping"
    token: str


class Pong(ClientMessage):
    """Heartbeat reply, piggybacking the connector's view of Tally.

    Bundling Tally's status onto the heartbeat means the app can show "Tally is
    closed" within one heartbeat instead of discovering it only when a report
    fails.
    """

    type: Literal["pong"] = "pong"
    token: str
    tally_online: bool
    companies_open: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


class JobRequest(ServerMessage):
    """A single read, addressed by registered query name."""

    type: Literal["job"] = "job"
    job_id: str
    query: str
    params: dict[str, Any] = Field(default_factory=dict)
    #: ``0`` disables caching for this job (the "pull to refresh" path).
    cache_ttl_seconds: float = 0.0
    #: Past this, the backend has already answered the phone; finishing the work
    #: would only burn Tally time on a result nobody is waiting for.
    deadline_seconds: float = 120.0


class JobError(BaseModel):
    code: str
    message: str
    user_message: str
    retryable: bool = False


class JobResult(ClientMessage):
    type: Literal["job_result"] = "job_result"
    job_id: str
    ok: bool
    #: ``json`` for a plain object, ``gzip+base64`` when compressed.
    encoding: Literal["json", "gzip+base64"] = "json"
    payload: Any = None
    error: JobError | None = None
    duration_ms: int = 0
    from_cache: bool = False

    @classmethod
    def success(
        cls,
        job_id: str,
        data: Any,
        *,
        duration_ms: int = 0,
        from_cache: bool = False,
    ) -> JobResult:
        encoding, payload = encode_payload(data)
        return cls(
            job_id=job_id,
            ok=True,
            encoding=encoding,
            payload=payload,
            duration_ms=duration_ms,
            from_cache=from_cache,
        )

    @classmethod
    def failure(cls, job_id: str, error: JobError, *, duration_ms: int = 0) -> JobResult:
        return cls(job_id=job_id, ok=False, error=error, duration_ms=duration_ms)

    def data(self) -> Any:
        """Decode :attr:`payload` regardless of how it was encoded."""
        return decode_payload(self.encoding, self.payload)


def encode_payload(data: Any) -> tuple[Literal["json", "gzip+base64"], Any]:
    """Compress large payloads, pass small ones through untouched."""
    raw = json.dumps(data, default=str, separators=(",", ":"))
    if len(raw) < COMPRESSION_THRESHOLD_BYTES:
        return "json", data
    packed = base64.b64encode(gzip.compress(raw.encode("utf-8"), compresslevel=6))
    return "gzip+base64", packed.decode("ascii")


def decode_payload(encoding: str, payload: Any) -> Any:
    if encoding == "json":
        return payload
    if encoding == "gzip+base64":
        return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))
    raise ValueError(f"unsupported payload encoding: {encoding!r}")


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


class StatusEvent(ClientMessage):
    """Unsolicited notice that the connector's view of Tally changed."""

    type: Literal["status"] = "status"
    tally_online: bool
    companies_open: list[str] = Field(default_factory=list)
    detail: str | None = None


class UpdateCommand(ServerMessage):
    """Tells a connector to install a new build now rather than at its next poll.

    Strictly speaking redundant -- ``latest_connector_version`` rides on every
    server frame, so a connector already has everything it needs to decide for
    itself. This exists because the two carry different *intent*: the stamped
    field is ambient ("this is what exists"), while receiving this frame is an
    instruction the backend can log and, later, target at one connector rather
    than the fleet.

    It deliberately carries no URL or checksum. The connector resolves those
    from the published manifest itself, which is the only path that hash-verifies
    the download -- accepting a URL over the socket would make a compromised or
    misconfigured backend able to install arbitrary code on a customer's PC.
    """

    type: Literal["update"] = "update"
    #: The version being asked for, so the connector can ignore a stale command.
    version: str
    #: Install without waiting for a convenient moment. Still waits for Tally
    #: to go idle -- that wait is not negotiable, see ``updater._wait_until_idle``.
    mandatory: bool = False
    reason: str = ""


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

#: A connector that ships more than this many lines in one frame is either
#: melting down or being replayed at us. The excess is dropped and counted, so
#: the support view says "1,842 lines dropped" instead of quietly showing a
#: partial history that reads as a quiet period.
MAX_LOG_ENTRIES_PER_BATCH = 500

#: Longest single line kept. A stack trace is worth having; a megabyte of
#: repr'd XML is the thing that would make remote logging cost more than the
#: shop's upload bandwidth is worth.
MAX_LOG_MESSAGE_CHARS = 4000


class LogEntry(BaseModel):
    """One line from a connector's log, on its way to the support view.

    ``logged_at`` is the *connector's* clock and is not to be trusted for
    ordering across machines -- a shop PC with a wrong system time is common
    enough that the backend stamps its own receipt time alongside this one and
    sorts by that. Keeping both is what makes "the customer's clock is six hours
    out" diagnosable rather than merely confusing.
    """

    model_config = ConfigDict(extra="ignore")

    logged_at: datetime
    level: str = "INFO"
    logger: str = ""
    message: str = ""


class LogBatch(ClientMessage):
    """Log lines pushed from a connector, batched.

    Batched rather than one frame per line for the obvious reason and one less
    obvious one: connectors sit behind asymmetric home broadband where upload is
    the bottleneck for report exports, and a frame per line would put the
    connector's own diagnostics in competition with the data the customer is
    waiting for.

    ``dropped`` reports lines the connector's bounded buffer discarded because
    the backend was unreachable or the connector was logging faster than it
    could ship. It is part of the frame rather than something the backend could
    infer: a support view that cannot tell "nothing happened" from "we lost the
    part where it happened" sends somebody down the wrong path.
    """

    type: Literal["log_batch"] = "log_batch"
    entries: list[LogEntry] = Field(default_factory=list)
    dropped: int = 0


#: Messages the connector may receive.
Inbound = Annotated[HelloAck | Ping | JobRequest | UpdateCommand, Field(discriminator="type")]
#: Messages the connector may send.
Outbound = Annotated[
    Hello | Pong | JobResult | StatusEvent | LogBatch, Field(discriminator="type")
]
