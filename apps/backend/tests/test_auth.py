"""Authentication, token rotation, and tenant isolation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.core.security import (
    create_token,
    hash_password,
    hash_token,
    verify_password,
)
from tally_backend.db.models import RefreshToken


async def test_register_returns_a_usable_token_pair(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "new@bhatiastores.in",
            "password": "a-sufficiently-long-password",
            "org_name": "New Shop",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access_token"] and body["refresh_token"]

    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "new@bhatiastores.in"
    # The registering user owns the organisation, or they could not pair a
    # connector and the account would be inert.
    assert me.json()["role"] == "owner"


async def test_duplicate_email_is_rejected(client: AsyncClient, registered) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "owner@bhatiastores.in",
            "password": "another-long-password",
            "org_name": "Copycat",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_short_passwords_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={"email": "weak@bhatiastores.in", "password": "short", "org_name": "Weak"},
    )
    assert response.status_code == 422


async def test_wrong_password_gives_the_same_answer_as_no_such_user(
    client: AsyncClient, registered
) -> None:
    """Neither response may reveal whether the account exists."""
    wrong_password = await client.post(
        "/v1/auth/login",
        json={"email": "owner@bhatiastores.in", "password": "not-the-password"},
    )
    no_such_user = await client.post(
        "/v1/auth/login",
        json={"email": "nobody@bhatiastores.in", "password": "not-the-password"},
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json()


async def test_refresh_rotates_and_burns_the_old_token(
    client: AsyncClient, registered
) -> None:
    original = registered["tokens"]["refresh_token"]

    first = await client.post("/v1/auth/refresh", json={"refresh_token": original})
    assert first.status_code == 200
    rotated = first.json()["refresh_token"]
    assert rotated != original

    replayed = await client.post("/v1/auth/refresh", json={"refresh_token": original})
    assert replayed.status_code == 401


async def test_replaying_a_spent_token_revokes_the_whole_family(
    app, client: AsyncClient, registered
) -> None:
    """Replay means the token leaked; every descendant must die with it.

    Losing a session is a far better outcome than letting an attacker who
    captured one refresh token keep renewing access indefinitely.
    """
    original = registered["tokens"]["refresh_token"]
    rotated = (
        await client.post("/v1/auth/refresh", json={"refresh_token": original})
    ).json()["refresh_token"]

    # The legitimate device's current token still works...
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": rotated})
    ).status_code == 200

    # ...until the stolen one is replayed, which poisons the family.
    replay = await client.post("/v1/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401

    async with app.state.session_factory() as session:
        live = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.revoked_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
    assert live == [], "every token in a replayed family must be revoked"


async def test_a_refresh_token_cannot_be_used_as_an_access_token(
    client: AsyncClient, registered
) -> None:
    refresh = registered["tokens"]["refresh_token"]
    response = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert response.status_code == 401


async def test_token_typ_confusion_is_rejected(client: AsyncClient, app, registered) -> None:
    """A validly-signed *refresh* JWT must not open an access-token door."""
    settings = app.state.settings
    forged = create_token(
        subject="whoever",
        token_type="refresh",
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=600,
        org_id="anything",
    )
    response = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_token_signed_with_another_secret_is_rejected(client: AsyncClient) -> None:
    forged = create_token(
        subject="attacker",
        token_type="access",
        secret="a-completely-different-secret-value",
        algorithm="HS256",
        ttl_seconds=600,
        org_id="someone-elses-org",
    )
    response = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_missing_and_malformed_headers_are_rejected(client: AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401
    assert (
        await client.get("/v1/auth/me", headers={"Authorization": "Basic abc"})
    ).status_code == 401


async def test_logout_all_revokes_every_device(client: AsyncClient, registered) -> None:
    second_device = (
        await client.post(
            "/v1/auth/login",
            json={
                "email": "owner@bhatiastores.in",
                "password": "a-sufficiently-long-password",
                "device_name": "iPad",
            },
        )
    ).json()

    assert (
        await client.post("/v1/auth/logout-all", headers=registered["headers"])
    ).status_code == 204

    for token in (
        registered["tokens"]["refresh_token"],
        second_device["refresh_token"],
    ):
        assert (
            await client.post("/v1/auth/refresh", json={"refresh_token": token})
        ).status_code == 401


async def test_login_accepts_browser_preflight(client: AsyncClient) -> None:
    response = await client.options(
        "/v1/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def test_password_hashing_round_trips() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong horse", stored)


def test_password_hashes_are_salted() -> None:
    """Identical passwords must not produce identical hashes."""
    assert hash_password("same-password") != hash_password("same-password")


def test_token_hash_is_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$argon2id$broken"])
def test_verifying_against_a_corrupt_hash_fails_closed(garbage: str) -> None:
    assert verify_password("anything", garbage) is False
