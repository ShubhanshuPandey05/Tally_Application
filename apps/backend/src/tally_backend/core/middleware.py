"""Cross-cutting HTTP middleware: request ids, timing, and rate limiting."""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and log completion.

    The id is echoed in the response header so a customer can quote it in a
    support message and it can be found in the logs -- which beats reconstructing
    an incident from a timestamp and a guess.
    """

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled error [%s] %s %s", request_id, request.method, request.url.path
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        response.headers["server-timing"] = f"app;dur={duration_ms:.1f}"

        if duration_ms > 2000:
            # Everything on the phone-facing path should be a snapshot read.
            # A slow one means something fell through to live Tally.
            logger.warning(
                "slow request [%s] %s %s took %.0fms",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter, per user or per IP.

    In-process and therefore per-instance: with N replicas the effective limit is
    N times the configured one. That is fine for its actual purpose here, which
    is absorbing a runaway client or a retry storm, not precise quota accounting.
    A Redis limiter is the upgrade path when limits need to be exact.
    """

    def __init__(
        self,
        app,  # noqa: ANN001 - Starlette's own signature
        *,
        default_per_minute: int,
        auth_per_minute: int,
    ) -> None:
        super().__init__(app)
        self._default = default_per_minute
        self._auth = auth_per_minute
        self._hits: dict[str, deque[float]] = {}

    def _limit_for(self, path: str) -> int:
        # Credential endpoints get a tighter bucket: they are the ones worth
        # brute-forcing, and no legitimate client logs in ten times a minute.
        return self._auth if path.startswith("/v1/auth/") else self._default

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if request.url.path in {"/v1/health", "/v1/ready"}:
            return await call_next(request)

        identity = self._identity(request)
        limit = self._limit_for(request.url.path)
        now = time.monotonic()

        window = self._hits.setdefault(identity, deque())
        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(60.0 - (now - window[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please wait a moment.",
                        "retryable": True,
                    }
                },
                headers={"retry-after": str(retry_after)},
            )

        window.append(now)
        self._prune(now)
        return await call_next(request)

    def _identity(self, request: Request) -> str:
        # Bucket by token when present so several users behind one shop's NAT do
        # not consume each other's allowance.
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return f"tok:{hash(auth[7:]) & 0xFFFFFFFF:08x}"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    def _prune(self, now: float) -> None:
        """Drop idle buckets so the dict cannot grow without bound."""
        if len(self._hits) < 10_000:
            return
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 120.0]:
            self._hits.pop(key, None)
