"""Checks the app's version on every request, and answers on every response.

The app used to poll ``manifest.json`` once per cold start. That meant a phone
left open on the dashboard all day never learned about a release, and a build
below the floor kept making requests the backend could not serve correctly until
someone happened to restart it.

Every request already carries the version now, so the check costs a dictionary
lookup and a tuple compare on a path that was going to run anyway. Two things
come out of that:

``Advisory headers on every response.``
    ``X-Latest-App-Version`` and ``X-Min-App-Version`` ride along on success and
    on failure alike, so the app learns about an optional update from ordinary
    traffic -- the dashboard it just loaded -- with no extra round trip and no
    poll interval to wait out.

``426 when the build is below the floor.``
    Not 400 and not 403: ``426 Upgrade Required`` is the status that means
    exactly this, and using a distinct one keeps "your app is too old" from
    being indistinguishable from "your request was wrong" in a log or a metric.

The error body carries the full release entry, so the app can start downloading
immediately from the same response that refused it, rather than fetching a
manifest to find out where to go.

Ordering note: this runs *outside* authentication, because an app too old to be
served is one whose login should also be refused -- otherwise the only path that
still works is the one that hands out tokens to a client that cannot use them.
It runs *inside* rate limiting, so a stuck old client cannot use the upgrade
path as a free unmetered endpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..services.releases import NO_OPINION, ReleaseCatalogue, Verdict

logger = logging.getLogger(__name__)

Handler = Callable[[Request], Awaitable[Response]]

#: What the client sends.
VERSION_HEADER = "x-app-version"
PLATFORM_HEADER = "x-app-platform"
BUILD_HEADER = "x-app-build"

#: What the backend answers with, on every response.
LATEST_HEADER = "x-latest-app-version"
MIN_HEADER = "x-min-app-version"
ACTION_HEADER = "x-app-update-action"

#: Exposed through CORS so Flutter web can read them; browsers hide every
#: non-safelisted response header otherwise, and the web build would be the one
#: client that never hears about an update.
ADVISORY_HEADERS = (LATEST_HEADER, MIN_HEADER, ACTION_HEADER)

#: Never refused, whatever version asks. Health checks are how a load balancer
#: decides the process is alive, and an old client still needs a route that
#: reports *why* it is being turned away -- refusing that too would leave it with
#: no way to tell "backend down" from "app too old".
ALWAYS_ALLOWED_PATHS = frozenset({"/v1/health", "/v1/ready"})


class ClientVersionMiddleware(BaseHTTPMiddleware):
    """Reads the app version off each request; stamps the verdict on each response."""

    def __init__(
        self,
        app,  # noqa: ANN001 - Starlette's own signature
        *,
        catalogue: ReleaseCatalogue,
        enforce: bool = True,
    ) -> None:
        super().__init__(app)
        self._catalogue = catalogue
        self._enforce = enforce

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        verdict = self._evaluate(request)
        # Stashed for handlers and for the audit log: "which version did this?"
        # is the first question asked about any report of wrong figures.
        request.state.app_version = verdict.current_version
        request.state.update_verdict = verdict

        if (
            verdict.is_required
            and self._enforce
            and request.url.path not in ALWAYS_ALLOWED_PATHS
        ):
            return self._refuse(request, verdict)

        response = await call_next(request)
        self._stamp(response, verdict)
        return response

    # -- internals -------------------------------------------------------

    def _evaluate(self, request: Request) -> Verdict:
        version = (request.headers.get(VERSION_HEADER) or "").strip()
        if not version:
            # Web builds, curl, the OpenAPI page, and every app build that
            # predates the header. No opinion is the only safe answer -- see
            # ``ReleaseCatalogue.evaluate``.
            return NO_OPINION

        platform = (request.headers.get(PLATFORM_HEADER) or "android").strip().lower()
        return self._catalogue.evaluate(platform, version)

    def _stamp(self, response: Response, verdict: Verdict) -> None:
        """Attach the advisory headers, if there is anything to advise."""
        if verdict.release is None:
            return
        response.headers[LATEST_HEADER] = verdict.latest_version
        if verdict.min_version:
            response.headers[MIN_HEADER] = verdict.min_version
        response.headers[ACTION_HEADER] = verdict.action.value

    def _refuse(self, request: Request, verdict: Verdict) -> JSONResponse:
        release = verdict.release
        logger.info(
            "refusing %s %s from app %s (below floor %s, latest %s)",
            request.method,
            request.url.path,
            verdict.current_version,
            verdict.min_version or "-",
            verdict.latest_version,
        )
        body = {
            "error": {
                "code": "update_required",
                "message": (
                    "This version of TallyFlow can no longer read your data "
                    "correctly. Please install the update to continue."
                ),
                # Not retryable: retrying the same request from the same build
                # can only fail again, and the app's retry loop would otherwise
                # spend its attempts before showing the user anything.
                "retryable": False,
                "detail": {"update": release.as_client_payload() if release else None},
            }
        }
        response = JSONResponse(status_code=426, content=body)
        self._stamp(response, verdict)
        return response
