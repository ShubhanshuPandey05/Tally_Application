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


class Hello(Message):
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


class HelloAck(Message):
    """Backend's answer to :class:`Hello`."""

    type: Literal["hello_ack"] = "hello_ack"
    accepted: bool
    session_id: str | None = None
    heartbeat_interval_seconds: float = 30.0
    reason: str | None = None


# --------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------


class Ping(Message):
    type: Literal["ping"] = "ping"
    token: str


class Pong(Message):
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


class JobRequest(Message):
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


class JobResult(Message):
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


class StatusEvent(Message):
    """Unsolicited notice that the connector's view of Tally changed."""

    type: Literal["status"] = "status"
    tally_online: bool
    companies_open: list[str] = Field(default_factory=list)
    detail: str | None = None


#: Messages the connector may receive.
Inbound = Annotated[HelloAck | Ping | JobRequest, Field(discriminator="type")]
#: Messages the connector may send.
Outbound = Annotated[Hello | Pong | JobResult | StatusEvent, Field(discriminator="type")]
