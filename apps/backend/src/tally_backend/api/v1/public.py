"""The unauthenticated surface: what the marketing site is allowed to ask for.

Kept in its own module so the answer to "what can someone with no credentials
read?" is a file rather than a search. Everything here is aggregate, and nothing
here takes a parameter — there is no argument a caller can vary, so there is
nothing to probe with.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response, status

from ..deps import PublicStatsDep, SessionDep, SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/config")
async def public_config(settings: SettingsDep) -> dict:
    """What an unauthenticated app needs to know before anyone signs in.

    One field today: whether this server has a demo to offer. The app asks
    rather than assuming, because "Explore the demo" on a deployment with no
    demo is a button that can only disappoint -- and which deployments have one
    is a server-side decision that changes without an app release.
    """
    return {"demo_available": bool(settings.demo_enabled and settings.demo_email)}


@router.get("/stats")
async def stats(
    session: SessionDep,
    settings: SettingsDep,
    service: PublicStatsDep,
    response: Response,
) -> dict:
    """Aggregate counts for the site's hero.

    Answers 404 when switched off, and 503 when the counts could not be
    computed. Neither returns zeros: the site prints these beside a claim about
    the product, and a figure that failed to compute must not arrive looking
    like a real count of nothing. "We cannot tell you" and "nobody is using it"
    are different statements, and the caller can only keep them apart if this
    endpoint refuses to blur them.
    """
    if not settings.public_stats_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        result = await service.get(session)
    except Exception:
        # Logged here and not re-raised: a page open to strangers must not be
        # able to put a stack trace in front of one, and the site handles the
        # 503 by simply not drawing the row.
        logger.exception("public stats could not be computed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "error": {
                "code": "stats_unavailable",
                "message": "Statistics are not available right now.",
                "retryable": True,
            }
        }

    # Matches the TTL the service itself uses, so a proxy or a browser holding
    # this response holds it for exactly as long as the process would have.
    response.headers["cache-control"] = f"public, max-age={settings.public_stats_ttl_seconds}"
    return result.as_dict()
