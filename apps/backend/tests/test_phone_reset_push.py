"""Proven phone numbers, emailed reset codes, and push notifications.

Firebase, the mail provider and FCM are all outside this process, so each is
replaced at its seam: the phone verifier on ``app.state``, the mailer's and the
push service's transports. Everything on our side of those seams -- what is
stored, what is refused, what is said to whom -- runs for real.
"""

from __future__ import annotations

import time
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select
from test_voucher_queue import forget_attempts, post, queue_for, rows

from tally_backend.core.errors import InvalidInput
from tally_backend.db.models import (
    Organisation,
    OrgStatus,
    PasswordResetCode,
    PushDevice,
    User,
    utc_now,
)
from tally_backend.services.phone_verification import FirebasePhoneVerifier
from tally_backend.services.push import SendResult

PASSWORD = "a-sufficiently-long-password"
PHONE = "+919876543210"


@pytest.fixture
def settings(settings):
    settings.firebase_project_id = "prime-analyst-test"
    settings.smtp_host = "smtp.test"
    settings.smtp_from = "Prime Analyst <no-reply@test>"
    return settings


class FakeVerifier:
    """Proves whatever number the token names: ``ok:+919876543210``."""

    enabled = True

    async def verified_phone(self, id_token: str) -> str:
        if not id_token.startswith("ok:"):
            raise InvalidInput("bad proof", user_message="We could not confirm your number.")
        return id_token[3:]


@pytest.fixture
def outbox(app) -> list[EmailMessage]:
    sent: list[EmailMessage] = []

    async def capture(message: EmailMessage) -> None:
        sent.append(message)

    app.state.mailer.transport = capture
    app.state.phone_verifier = FakeVerifier()
    return sent


async def sign_up(client: AsyncClient, email: str = "owner@shop.in", **fields) -> Any:
    return await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "Shop Owner",
            "org_name": "Gupta Electricals",
            **fields,
        },
    )


def bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------
# Proving the phone at signup
# --------------------------------------------------------------------------


async def test_a_proven_number_is_marked_proven(client, outbox) -> None:
    response = await sign_up(client, phone="98765 43210", phone_token=f"ok:{PHONE}")
    assert response.status_code == 201, response.text

    me = (await client.get("/v1/auth/me", headers=bearer(response))).json()
    assert me["phone"] == PHONE
    assert me["phone_verified"] is True
    assert me["subscription"]["is_trial"] is True


async def test_a_code_sent_to_another_number_is_refused(client, outbox) -> None:
    response = await sign_up(client, phone=PHONE, phone_token="ok:+919999999999")

    assert response.status_code == 400
    assert "different number" in response.json()["error"]["message"]


async def test_without_a_proof_the_number_is_kept_unproven(client, outbox) -> None:
    """Optional mode: the build before codes still signs up as it did."""
    response = await sign_up(client, phone=PHONE)
    assert response.status_code == 201

    me = (await client.get("/v1/auth/me", headers=bearer(response))).json()
    assert me["phone_verified"] is False
    assert me["subscription"]["is_trial"] is True


async def test_required_proof_refuses_a_typed_number_and_says_to_update(
    app, client, outbox, settings
) -> None:
    settings.require_verified_phone = True

    response = await sign_up(client, phone=PHONE)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["detail"]["reason"] == "phone_proof_required"
    assert "update" in error["message"].lower()


async def test_required_proof_withholds_the_trial_from_an_unproven_number(
    app, client, outbox, settings
) -> None:
    """An account made before codes, signing in after they became required,
    does not get a trial for a number nobody proved."""
    response = await sign_up(client, phone=PHONE)
    org_id = (await client.get("/v1/auth/me", headers=bearer(response))).json()["org_id"]
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.status = OrgStatus.PENDING
        org.trial_granted_at = None
        await session.commit()

    settings.require_verified_phone = True
    signed_in = await client.post(
        "/v1/auth/login", json={"email": "owner@shop.in", "password": PASSWORD}
    )
    me = (await client.get("/v1/auth/me", headers=bearer(signed_in))).json()
    assert me["subscription"]["status"] == "pending"

    proven = await client.post(
        "/v1/auth/me/phone",
        json={"phone": PHONE, "phone_token": f"ok:{PHONE}"},
        headers=bearer(signed_in),
    )
    assert proven.status_code == 200, proven.text
    assert proven.json()["phone_verified"] is True
    assert proven.json()["subscription"]["is_trial"] is True


async def test_a_new_number_loses_the_old_proof(app, client, outbox) -> None:
    response = await sign_up(client, phone=PHONE, phone_token=f"ok:{PHONE}")

    changed = await client.post(
        "/v1/auth/me/phone", json={"phone": "+919811112222"}, headers=bearer(response)
    )

    assert changed.status_code == 200
    assert changed.json()["phone_verified"] is False


async def test_public_config_says_what_this_server_can_do(client, outbox, settings) -> None:
    config = (await client.get("/v1/public/config")).json()
    assert config["phone_verification"] == "optional"
    assert config["password_reset"] is True

    settings.require_verified_phone = True
    assert (await client.get("/v1/public/config")).json()["phone_verification"] == "required"


# --------------------------------------------------------------------------
# The Firebase token check itself
# --------------------------------------------------------------------------

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def firebase_token(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://securetoken.google.com/prime-analyst-test",
        "aud": "prime-analyst-test",
        "sub": "firebase-uid",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now - 10,
        "phone_number": PHONE,
        "firebase": {"sign_in_provider": "phone"},
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def verifier() -> FirebasePhoneVerifier:
    checker = FirebasePhoneVerifier("prime-analyst-test")
    # Google's keys stand in for the download, so no test reaches the network.
    checker._keys = {"k1": _KEY.public_key()}
    checker._keys_expire_at = time.monotonic() + 3600
    return checker


async def test_a_genuine_token_gives_its_number(verifier) -> None:
    assert await verifier.verified_phone(firebase_token()) == PHONE


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "somebody-elses-project"},
        {"iss": "https://securetoken.google.com/somebody-elses-project"},
        {"firebase": {"sign_in_provider": "password"}},
        {"auth_time": int(time.time()) - 3600},
        {"phone_number": None},
        {"exp": int(time.time()) - 600},
    ],
)
async def test_a_token_that_proves_something_else_is_refused(verifier, overrides) -> None:
    with pytest.raises(InvalidInput):
        await verifier.verified_phone(firebase_token(**overrides))


async def test_a_token_signed_by_another_key_is_refused(verifier) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        jwt.decode(firebase_token(), options={"verify_signature": False}),
        other,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(InvalidInput):
        await verifier.verified_phone(forged)


# --------------------------------------------------------------------------
# Forgotten passwords
# --------------------------------------------------------------------------


def code_in(message: EmailMessage) -> str:
    return message["Subject"].split()[0]


async def ask_for_code(client, email: str = "owner@shop.in"):
    return await client.post("/v1/auth/password-reset", json={"email": email})


async def test_a_code_is_emailed_and_resets_the_password(client, outbox) -> None:
    first = await sign_up(client)
    refresh_before = first.json()["refresh_token"]

    asked = await ask_for_code(client)
    assert asked.status_code == 202
    assert len(outbox) == 1
    assert outbox[0]["To"] == "owner@shop.in"

    reset = await client.post(
        "/v1/auth/password-reset/confirm",
        json={
            "email": "owner@shop.in",
            "code": code_in(outbox[0]),
            "new_password": "a-brand-new-long-password",
        },
    )
    assert reset.status_code == 204, reset.text

    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": "owner@shop.in", "password": "a-brand-new-long-password"},
    )
    assert signed_in.status_code == 200
    # Every earlier session is gone: the reset may be because somebody got in.
    stale = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_before})
    assert stale.status_code == 401


async def test_an_unknown_address_is_answered_the_same_and_sent_nothing(
    client, outbox
) -> None:
    asked = await ask_for_code(client, "nobody@nowhere.in")

    assert asked.status_code == 202
    assert asked.json() == {"status": "sent"}
    assert outbox == []


async def test_a_code_works_once(client, outbox) -> None:
    await sign_up(client)
    await ask_for_code(client)
    body = {
        "email": "owner@shop.in",
        "code": code_in(outbox[0]),
        "new_password": "a-brand-new-long-password",
    }
    assert (await client.post("/v1/auth/password-reset/confirm", json=body)).status_code == 204

    again = await client.post("/v1/auth/password-reset/confirm", json=body)
    assert again.status_code == 400
    assert "expired or was already used" in again.json()["error"]["message"]


async def test_wrong_guesses_burn_the_code(app, client, outbox, settings) -> None:
    await sign_up(client)
    await ask_for_code(client)
    right = code_in(outbox[0])
    wrong = "000000" if right != "000000" else "111111"

    for _ in range(settings.password_reset_max_attempts):
        refused = await client.post(
            "/v1/auth/password-reset/confirm",
            json={"email": "owner@shop.in", "code": wrong, "new_password": "x" * 12},
        )
        assert refused.status_code == 400

    # The right code no longer helps: the guesses were counted even though
    # every one of those requests failed.
    late = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "owner@shop.in", "code": right, "new_password": "x" * 12},
    )
    assert late.status_code == 400


async def test_asking_again_straight_away_sends_nothing_new(client, outbox) -> None:
    await sign_up(client)
    await ask_for_code(client)
    again = await ask_for_code(client)

    assert again.status_code == 202
    assert len(outbox) == 1


async def test_a_newer_code_replaces_the_older(app, client, outbox, settings) -> None:
    await sign_up(client)
    settings.password_reset_resend_seconds = 0
    await ask_for_code(client)
    await ask_for_code(client)
    assert len(outbox) == 2

    old = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "owner@shop.in", "code": code_in(outbox[0]), "new_password": "x" * 12},
    )
    new = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "owner@shop.in", "code": code_in(outbox[1]), "new_password": "x" * 12},
    )
    if code_in(outbox[0]) != code_in(outbox[1]):
        assert old.status_code == 400
    assert new.status_code == 204


async def test_an_expired_code_is_refused(app, client, outbox) -> None:
    await sign_up(client)
    await ask_for_code(client)
    async with app.state.session_factory() as session:
        row = (await session.execute(select(PasswordResetCode))).scalars().one()
        row.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    refused = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "owner@shop.in", "code": code_in(outbox[0]), "new_password": "x" * 12},
    )
    assert refused.status_code == 400


async def test_the_code_is_not_stored(app, client, outbox) -> None:
    await sign_up(client)
    await ask_for_code(client)
    async with app.state.session_factory() as session:
        row = (await session.execute(select(PasswordResetCode))).scalars().one()
    assert code_in(outbox[0]) not in row.code_hash


async def test_reset_also_clears_an_admin_issued_password(app, client, outbox) -> None:
    await sign_up(client)
    async with app.state.session_factory() as session:
        user = (await session.execute(select(User))).scalars().one()
        user.must_change_password = True
        await session.commit()

    await ask_for_code(client)
    await client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "owner@shop.in", "code": code_in(outbox[0]), "new_password": "x" * 12},
    )
    signed_in = await client.post(
        "/v1/auth/login", json={"email": "owner@shop.in", "password": "x" * 12}
    )
    me = (await client.get("/v1/auth/me", headers=bearer(signed_in))).json()
    assert me["must_change_password"] is False


async def test_without_email_set_up_reset_says_so(client, outbox, settings) -> None:
    settings.smtp_host = None

    asked = await ask_for_code(client)

    assert asked.status_code == 503
    assert (await client.get("/v1/public/config")).json()["password_reset"] is False


# --------------------------------------------------------------------------
# Push notifications
# --------------------------------------------------------------------------

TOKEN = "fcm-token-" + "x" * 40


async def devices(app) -> list[PushDevice]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(PushDevice))).scalars().all())


async def test_a_phone_registers_and_unregisters(app, client, registered) -> None:
    added = await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=registered["headers"],
    )
    assert added.status_code == 204
    assert [d.token for d in await devices(app)] == [TOKEN]

    removed = await client.post(
        "/v1/push/devices/remove", json={"token": TOKEN}, headers=registered["headers"]
    )
    assert removed.status_code == 204
    assert await devices(app) == []


async def test_a_token_moves_to_whoever_signs_in_on_that_phone(
    app, client, registered, outbox
) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=registered["headers"],
    )
    other = await sign_up(client, email="other@shop.in")
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=bearer(other),
    )

    rows_now = await devices(app)
    assert len(rows_now) == 1
    me = (await client.get("/v1/auth/me", headers=bearer(other))).json()
    assert rows_now[0].user_id == me["id"]


async def test_one_person_cannot_remove_anothers_token(
    app, client, registered, outbox
) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=registered["headers"],
    )
    stranger = await sign_up(client, email="stranger@shop.in")
    await client.post(
        "/v1/push/devices/remove", json={"token": TOKEN}, headers=bearer(stranger)
    )

    assert len(await devices(app)) == 1


@pytest.fixture
def pushed(app) -> list[tuple[str, dict[str, Any]]]:
    sent: list[tuple[str, dict[str, Any]]] = []

    async def capture(token: str, message: dict[str, Any]) -> SendResult:
        sent.append((token, message))
        return SendResult(ok=True)

    push = app.state.push
    push._account = {"project_id": "test", "client_email": "x", "private_key": "x"}
    push.transport = capture
    return sent


def queue_with_push(app):
    queue = queue_for(app)
    queue._push = app.state.push
    return queue


async def test_a_queued_entry_reaching_tally_tells_its_author(
    app, client, linked_company, fake_connector, pushed
) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=linked_company["headers"],
    )
    fake_connector.online = False
    await post(client, linked_company)
    forget_attempts(fake_connector)
    fake_connector.online = True

    await queue_with_push(app).drain(linked_company["connector_id"])

    assert len(pushed) == 1
    token, message = pushed[0]
    assert token == TOKEN
    assert message["notification"]["title"] == "Your receipt reached TallyPrime"
    # Nothing from the books on a lock screen: not the party, not the amount.
    text = str(message)
    assert "Ram" not in text
    assert "5000" not in text
    assert message["data"]["kind"] == "queued_entry"


async def test_a_queued_entry_that_fails_says_so(
    app, client, linked_company, fake_connector, pushed
) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=linked_company["headers"],
    )
    fake_connector.online = False
    await post(client, linked_company)
    forget_attempts(fake_connector)
    fake_connector.online = True
    fake_connector.write_fails_with = "tally_error"

    await queue_with_push(app).drain(linked_company["connector_id"])

    assert pushed[0][1]["notification"]["title"] == "Your receipt was not saved"


async def test_an_entry_still_waiting_sends_nothing(
    app, client, linked_company, fake_connector, pushed
) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=linked_company["headers"],
    )
    fake_connector.online = False
    await post(client, linked_company)

    await queue_with_push(app).drain(linked_company["connector_id"])

    assert pushed == []
    assert (await rows(app, linked_company["company_id"]))[0].state == "waiting"


async def test_an_uninstalled_app_is_forgotten(app, client, registered, pushed) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=registered["headers"],
    )

    async def gone(token: str, message: dict[str, Any]) -> SendResult:
        return SendResult(ok=False, gone=True)

    app.state.push.transport = gone
    user_id = (await devices(app))[0].user_id
    sent = await app.state.push.notify_user(user_id, title="t", body="b")

    assert sent == 0
    assert await devices(app) == []


async def test_a_failing_transport_never_raises(app, client, registered, pushed) -> None:
    await client.post(
        "/v1/push/devices",
        json={"token": TOKEN, "platform": "android"},
        headers=registered["headers"],
    )

    async def broken(token: str, message: dict[str, Any]) -> SendResult:
        raise RuntimeError("FCM is down")

    app.state.push.transport = broken
    user_id = (await devices(app))[0].user_id

    assert await app.state.push.notify_user(user_id, title="t", body="b") == 0


async def test_a_proof_sent_to_a_server_not_checking_is_set_aside(
    app, client, outbox, settings
) -> None:
    app.state.phone_verifier = FirebasePhoneVerifier(None)

    response = await sign_up(client, phone=PHONE, phone_token="anything")

    assert response.status_code == 201
    me = (await client.get("/v1/auth/me", headers=bearer(response))).json()
    assert me["phone_verified"] is False
