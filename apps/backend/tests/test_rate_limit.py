"""The limiter's buckets, driven with the numbers production actually runs.

The rest of the suite raises both limits to 10,000 so that no test is flaky
because of throttling -- which is right, and is also why the bug this file
exists for went unnoticed until a customer could not sign in. So these build the
middleware directly over a bare Starlette app: no database, no auth, and the
real ceilings.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tally_backend.core.middleware import RateLimitMiddleware

#: What deploy/prod/prod.env.example sets.
DEFAULT_PER_MINUTE = 120
AUTH_PER_MINUTE = 10


async def _ok(_request):
    return JSONResponse({"ok": True})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/v1/dashboard", _ok),
            Route("/v1/health", _ok),
            Route("/v1/auth/login", _ok, methods=["POST"]),
            Route("/v1/auth/register", _ok, methods=["POST"]),
            Route("/v1/auth/refresh", _ok, methods=["POST"]),
        ]
    )
    app.add_middleware(
        RateLimitMiddleware,
        default_per_minute=DEFAULT_PER_MINUTE,
        auth_per_minute=AUTH_PER_MINUTE,
    )
    return TestClient(app)


def test_ordinary_traffic_does_not_spend_the_sign_in_allowance(client: TestClient) -> None:
    """The bug this file was written for.

    One counter per identity, checked against whichever ceiling the current path
    carried, meant the tightest ceiling governed all the traffic: a phone that
    had just loaded a dashboard was refused its owner's *first* sign-in. The
    person hitting it has done nothing wrong and has nothing to wait for, which
    is the worst shape a rate limit can take.
    """
    for _ in range(AUTH_PER_MINUTE * 2):
        assert client.get("/v1/dashboard").status_code == 200

    assert client.post("/v1/auth/login").status_code == 200
    assert client.post("/v1/auth/register").status_code == 200


def test_renewing_a_session_does_not_spend_it_either(client: TestClient) -> None:
    """Several phones on one shop's wifi refresh continuously, and share an IP.

    A refresh token is high-entropy and replaying one revokes the family, so
    guessing is not the threat it defends against -- but sharing a bucket with
    sign-in would let routine housekeeping lock a real person out.
    """
    for _ in range(AUTH_PER_MINUTE * 2):
        assert client.post("/v1/auth/refresh").status_code == 200

    assert client.post("/v1/auth/login").status_code == 200


def test_the_credential_bucket_still_closes(client: TestClient) -> None:
    """Splitting the buckets must not have removed the protection."""
    for _ in range(AUTH_PER_MINUTE):
        assert client.post("/v1/auth/login").status_code == 200

    refused = client.post("/v1/auth/login")
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    # Told when to come back, rather than left to guess.
    assert 1 <= int(refused.headers["retry-after"]) <= 60


def test_sign_up_and_sign_in_share_one_ceiling(client: TestClient) -> None:
    """Both trade a guessable secret for a session, so both are worth guessing.

    Separate buckets here would double the attempts available to somebody
    working through a password list.
    """
    for _ in range(AUTH_PER_MINUTE):
        assert client.post("/v1/auth/register").status_code == 200

    assert client.post("/v1/auth/login").status_code == 429


def test_the_default_bucket_is_the_generous_one(client: TestClient) -> None:
    for _ in range(DEFAULT_PER_MINUTE):
        assert client.get("/v1/dashboard").status_code == 200

    assert client.get("/v1/dashboard").status_code == 429


def test_liveness_is_never_throttled(client: TestClient) -> None:
    """A probe that gets a 429 takes the container out of the load balancer."""
    for _ in range(DEFAULT_PER_MINUTE * 2):
        assert client.get("/v1/health").status_code == 200


def test_one_shop_does_not_spend_another_customers_allowance(client: TestClient) -> None:
    """Bucketed by token when there is one, so a shared NAT is not a shared quota."""
    hot = {"authorization": "Bearer aaa"}
    cold = {"authorization": "Bearer bbb"}

    for _ in range(DEFAULT_PER_MINUTE):
        assert client.get("/v1/dashboard", headers=hot).status_code == 200

    assert client.get("/v1/dashboard", headers=hot).status_code == 429
    assert client.get("/v1/dashboard", headers=cold).status_code == 200
