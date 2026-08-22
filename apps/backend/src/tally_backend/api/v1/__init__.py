"""Version 1 of the public API.

Versioned from the first release. Phones update on their own schedule -- some
never -- so a breaking change has to be able to live alongside the old shape
rather than replace it.
"""

from fastapi import APIRouter

from . import (
    auth,
    companies,
    connector_ws,
    connectors,
    data,
    health,
    portal,
    portal_logs,
    public,
    sync,
    team,
)

api_router = APIRouter()
api_router.include_router(health.router)
#: Aggregate counts for the marketing site. The only route that reads customer
#: data without a token, which is why it lives in a module of its own -- what an
#: anonymous caller can see should be a file, not the result of a search.
api_router.include_router(public.router)
api_router.include_router(auth.router)
api_router.include_router(team.router)
#: The management portal. Same version prefix, entirely separate authority --
#: a customer's token is refused by `typ` before any handler in it runs.
api_router.include_router(portal.router)
#: The portal's support view. Same prefix and the same authority as above, kept
#: in its own module because deciding what an account is entitled to and finding
#: out why one customer's PC stopped talking are different jobs.
api_router.include_router(portal_logs.router)
api_router.include_router(connectors.router)
api_router.include_router(companies.router)
api_router.include_router(sync.router)
api_router.include_router(data.router)
#: The connector's outbound WebSocket. Not part of the phone-facing surface.
api_router.include_router(connector_ws.router)

__all__ = ["api_router"]
