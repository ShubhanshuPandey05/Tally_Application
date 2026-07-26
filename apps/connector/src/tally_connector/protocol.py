"""Re-export of the wire contract, which now lives in :mod:`tally_core.protocol`.

The protocol is shared by the connector *and* the backend, so it belongs in the
package both depend on. Having the backend import it from the connector would
force a server deployment to pull in a Windows desktop application.

Kept as an alias rather than deleted so that ``from .protocol import ...`` inside
the connector -- and any connector build already in the field -- keeps working.
"""

from __future__ import annotations

from tally_core.protocol import (
    COMPRESSION_THRESHOLD_BYTES,
    PROTOCOL_VERSION,
    Hello,
    HelloAck,
    HostInfo,
    Inbound,
    JobError,
    JobRequest,
    JobResult,
    Message,
    Outbound,
    Ping,
    Pong,
    QueryCapability,
    StatusEvent,
    decode_payload,
    encode_payload,
    sign_handshake,
    utc_now,
    verify_handshake,
)

__all__ = [
    "COMPRESSION_THRESHOLD_BYTES",
    "PROTOCOL_VERSION",
    "Hello",
    "HelloAck",
    "HostInfo",
    "Inbound",
    "JobError",
    "JobRequest",
    "JobResult",
    "Message",
    "Outbound",
    "Ping",
    "Pong",
    "QueryCapability",
    "StatusEvent",
    "decode_payload",
    "encode_payload",
    "sign_handshake",
    "utc_now",
    "verify_handshake",
]
