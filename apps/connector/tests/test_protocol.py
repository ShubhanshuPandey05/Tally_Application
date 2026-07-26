"""Wire contract tests: handshake auth, payload encoding, forward compatibility."""

from __future__ import annotations

import time

import pytest

from tally_connector.protocol import (
    COMPRESSION_THRESHOLD_BYTES,
    Hello,
    HelloAck,
    HostInfo,
    JobError,
    JobRequest,
    JobResult,
    QueryCapability,
    decode_payload,
    encode_payload,
    sign_handshake,
    verify_handshake,
)

SECRET = "s3cret-pairing-key"

HOST = HostInfo(
    hostname="SHOP-PC",
    os="Windows 11",
    connector_version="0.1.0",
    python_version="3.12.10",
)


def make_hello(secret: str = SECRET) -> Hello:
    return Hello.signed(
        connector_id="con_abc123",
        secret=secret,
        host=HOST,
        capabilities=[QueryCapability(name="companies.list", version=1)],
    )


# --------------------------------------------------------------------------
# Handshake authentication
# --------------------------------------------------------------------------


def test_valid_handshake_verifies():
    assert verify_handshake(make_hello(), secret=SECRET) is True


def test_secret_never_appears_on_the_wire():
    """The whole point of signing: possession is proved without disclosure."""
    assert SECRET not in make_hello().model_dump_json()


def test_wrong_secret_is_rejected():
    assert verify_handshake(make_hello(secret="wrong"), secret=SECRET) is False


def test_tampered_connector_id_is_rejected():
    """A stolen signature must not be reusable for a different connector."""
    hello = make_hello()
    forged = hello.model_copy(update={"connector_id": "con_victim"})
    assert verify_handshake(forged, secret=SECRET) is False


def test_stale_handshake_is_rejected():
    """Bounds the replay window even if a signed frame is captured."""
    hello = make_hello()
    old = hello.model_copy(update={"issued_at": int(time.time()) - 3600})
    assert verify_handshake(old, secret=SECRET) is False


def test_future_dated_handshake_is_rejected():
    hello = make_hello()
    future = hello.model_copy(update={"issued_at": int(time.time()) + 3600})
    assert verify_handshake(future, secret=SECRET) is False


def test_nonce_differs_per_handshake():
    assert make_hello().nonce != make_hello().nonce


def test_signature_construction_is_stable():
    """Backend and connector must derive byte-identical canonical strings."""
    assert sign_handshake(
        connector_id="con_abc123", nonce="deadbeef", issued_at=1700000000, secret=SECRET
    ) == sign_handshake(
        connector_id="con_abc123", nonce="deadbeef", issued_at=1700000000, secret=SECRET
    )


# --------------------------------------------------------------------------
# Payload encoding
# --------------------------------------------------------------------------


def test_small_payloads_are_sent_uncompressed():
    encoding, payload = encode_payload({"total": "1200.00"})
    assert encoding == "json"
    assert payload == {"total": "1200.00"}


def test_large_payloads_are_compressed_and_round_trip():
    big = [{"name": f"Ledger {i}", "balance": "1234.56"} for i in range(2000)]
    encoding, payload = encode_payload(big)

    assert encoding == "gzip+base64"
    assert len(payload) < COMPRESSION_THRESHOLD_BYTES * 4  # meaningfully smaller
    assert decode_payload(encoding, payload) == big


def test_job_result_round_trips_through_json():
    """The real path: serialise to a frame, parse it back, read the data."""
    data = [{"name": f"Item {i}", "qty": i} for i in range(1000)]
    frame = JobResult.success("job-1", data, duration_ms=42).model_dump_json()

    parsed = JobResult.model_validate_json(frame)
    assert parsed.ok is True
    assert parsed.encoding == "gzip+base64"
    assert parsed.data() == data


def test_unsupported_encoding_is_refused():
    with pytest.raises(ValueError, match="unsupported payload encoding"):
        decode_payload("brotli", "x")


def test_failure_result_carries_a_user_message():
    result = JobResult.failure(
        "job-2",
        JobError(
            code="tally_unreachable",
            message="connect refused",
            user_message="TallyPrime isn't responding.",
            retryable=True,
        ),
    )
    parsed = JobResult.model_validate_json(result.model_dump_json())
    assert parsed.ok is False
    assert parsed.error.user_message == "TallyPrime isn't responding."
    assert parsed.error.retryable is True


# --------------------------------------------------------------------------
# Compatibility
# --------------------------------------------------------------------------


def test_unknown_fields_are_ignored_not_fatal():
    """A newer backend adding a field must not break older connectors."""
    job = JobRequest.model_validate(
        {
            "type": "job",
            "job_id": "j1",
            "query": "companies.list",
            "params": {},
            "priority": "high",  # from a future backend
        }
    )
    assert job.job_id == "j1"


def test_messages_declare_the_protocol_version():
    assert JobRequest(job_id="j", query="companies.list").protocol_version == 1
    assert HelloAck(accepted=True, session_id="s").protocol_version == 1


def test_job_defaults_are_conservative():
    """Uncached and time-bounded unless the backend says otherwise."""
    job = JobRequest(job_id="j", query="companies.list")
    assert job.cache_ttl_seconds == 0.0
    assert job.deadline_seconds == 120.0
