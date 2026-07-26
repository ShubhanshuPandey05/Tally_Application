"""Error taxonomy and the single JSON error shape the app sees.

Every failure the phone can encounter carries a ``user_message`` written for a
business owner, not a developer. "Your Tally PC is offline" is actionable;
"WebSocket closed unexpectedly" is not, and a read-only dashboard that shows
stack-trace language reads as broken software.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base for expected, user-visible failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"
    user_message: str = "Something went wrong."
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        user_message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.user_message)
        self.message = message or self.user_message
        if user_message:
            self.user_message = user_message
        self.detail = detail or {}

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.user_message,
                "retryable": self.retryable,
            }
        }
        if self.detail:
            body["error"]["detail"] = self.detail
        return body


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    user_message = "Please sign in again."


class PermissionDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    user_message = "You do not have access to this."


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    user_message = "That was not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    user_message = "That already exists."


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    user_message = "Too many requests. Please wait a moment."
    retryable = True


class ConnectorOffline(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "connector_offline"
    user_message = "Your Tally PC is offline. Data shown was last updated earlier."
    retryable = True


class TallyUnavailable(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "tally_unavailable"
    user_message = "TallyPrime is not responding on your PC. Please make sure it is open."
    retryable = True


class ConnectorTimeout(AppError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "connector_timeout"
    user_message = "TallyPrime is taking too long to respond. Please try again."
    retryable = True


class UnsupportedByConnector(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "connector_outdated"
    user_message = "Your Tally Connector needs updating to show this report."


class NoDataYet(AppError):
    """Nothing cached and the connector cannot be reached to fetch it.

    Distinct from :class:`ConnectorOffline` because the app renders it very
    differently: offline-with-data shows a staleness banner over real numbers,
    offline-without-data must show a first-run empty state.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "no_data_yet"
    user_message = "We have not been able to read this from Tally yet."
    retryable = True


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Give request-validation failures the same envelope as everything else.

    FastAPI's default 422 body is a bare ``detail`` list, which is a second error
    shape for the app to parse -- and the one it will forget to handle. The field
    errors are preserved under ``detail`` for developers; ``message`` stays
    generic because a Pydantic error string is not something to show a shop owner.
    """
    fields = [
        {"field": ".".join(str(part) for part in err.get("loc", [])[1:]), "problem": err.get("msg")}
        for err in exc.errors()
    ]
    return JSONResponse(
        # 422 spelled numerically: Starlette renamed the constant, and pinning
        # the name would break on one version or the other. The code is stable.
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "Some of the information sent was not valid.",
                "retryable": False,
                "detail": {"fields": fields},
            }
        },
    )


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Give framework-raised errors the same envelope as ours.

    Without this the app would have to parse two different error shapes, and the
    one it forgets to handle is always the one that fires in production.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": _CODES.get(exc.status_code, "error"),
                "message": exc.detail if isinstance(exc.detail, str) else "Request failed.",
                "retryable": exc.status_code >= 500,
            }
        },
        headers=getattr(exc, "headers", None),
    )


_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
    504: "timeout",
}
