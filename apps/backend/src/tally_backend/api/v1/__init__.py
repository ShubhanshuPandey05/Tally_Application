"""Version 1 of the public API.

Versioned from the first release. Phones update on their own schedule -- some
never -- so a breaking change has to be able to live alongside the old shape
rather than replace it.
"""

from fastapi import APIRouter

from . import auth, companies, connector_ws, connectors, data, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(connectors.router)
api_router.include_router(companies.router)
api_router.include_router(data.router)
#: The connector's outbound WebSocket. Not part of the phone-facing surface.
api_router.include_router(connector_ws.router)

__all__ = ["api_router"]
