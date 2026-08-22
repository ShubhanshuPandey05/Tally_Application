"""Application factory and entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import api_router
from .config import Settings, get_settings
from .core.crypto import SecretBox
from .core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
)
from .core.middleware import RateLimitMiddleware, RequestContextMiddleware
from .core.version_middleware import ADVISORY_HEADERS, ClientVersionMiddleware
from .db.session import create_all, create_engine, create_session_factory
from .hub import ConnectorHub
from .services.connector_logs import ConnectorLogIngest
from .services.logs import LogWriter, ServerLogStore, install_capture
from .services.portal import bootstrap_owner
from .services.public_stats import PublicStatsService
from .services.refresher import SnapshotRefresher
from .services.releases import build_catalogue
from .services.sync import SyncCoordinator

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Every connector heartbeat is an INFO line in these loggers. At a thousand
    # connectors that is the only thing anyone would ever see in the logs.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )


def build_log_store(settings: Settings) -> ServerLogStore:
    """Create the ring and start capturing into it.

    Done at app construction rather than in the lifespan on purpose: the
    interesting lines are the ones emitted while the application is coming up,
    and a capture that starts after startup finishes is a capture that misses
    every failure to start.
    """
    store = ServerLogStore(
        capacity=settings.log_ring_capacity,
        instance_id=settings.instance_id,
        persist_from=settings.log_persist_level,
    )
    install_capture(store, level=getattr(logging, settings.log_capture_level.upper(), logging.INFO))
    return store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.settings = settings
        app.state.secret_box = SecretBox(settings.encryption_keys)

        if settings.database_url.startswith("sqlite"):
            # Convenience for dev and tests only. Postgres deployments run
            # Alembic, so that a schema change does not silently skip a
            # migration and leave the table shape wrong.
            await create_all(engine)

        # After the schema exists and before anything serves traffic. Silent
        # unless configured, and never overwrites an account that already
        # exists -- see `services.portal.bootstrap_owner`.
        await bootstrap_owner(app.state.session_factory, settings)

        hub = ConnectorHub(settings)
        app.state.hub = hub
        await hub.start()

        # Constructed before the refresher because the refresher hands companies
        # to it: a sweep resumes interrupted backfills and runs deltas.
        sync = SyncCoordinator(app.state.session_factory, hub, settings)
        app.state.sync = sync

        refresher = SnapshotRefresher(app.state.session_factory, hub, settings, sync)
        app.state.refresher = refresher
        await refresher.start()

        # Both drain buffers that were already filling before the loop existed,
        # so nothing logged during construction or startup is lost.
        log_writer = LogWriter(
            app.state.session_factory,
            app.state.log_store,
            retention_days=settings.log_retention_days,
            connector_retention_days=settings.connector_log_retention_days,
        )
        app.state.log_writer = log_writer
        await log_writer.start()

        if settings.connector_logs_enabled:
            connector_logs = ConnectorLogIngest(
                app.state.session_factory, capacity=settings.connector_log_buffer
            )
            app.state.connector_logs = connector_logs
            await connector_logs.start()

        logger.info(
            "TallyFlow backend %s ready (instance=%s, env=%s, routing=%s)",
            __version__,
            settings.instance_id,
            settings.environment,
            "distributed" if hub.bus.is_distributed else "single-instance",
        )
        try:
            yield
        finally:
            # Logs are stopped last, so the shutdown of everything below is
            # itself recorded rather than lost to the shutdown of the recorder.
            await refresher.stop()
            # Stopped after the refresher so a sweep cannot spawn a backfill
            # into a coordinator that has already shut down.
            await sync.stop()
            await hub.stop()
            if getattr(app.state, "connector_logs", None) is not None:
                await app.state.connector_logs.stop()
            await log_writer.stop()
            await engine.dispose()

    app = FastAPI(
        title="TallyFlow API",
        version=__version__,
        description="Read-only mobile dashboard for TallyPrime.",
        lifespan=lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )

    # Settings are attached before startup too, so that tests which never enter
    # the lifespan can still construct dependencies.
    app.state.settings = settings
    app.state.log_store = build_log_store(settings)
    # Set to None rather than left unset: `getattr(state, name, None)` on a
    # Starlette State raises AttributeError for a name never assigned, so the
    # "logging is off" path has to be a real value.
    app.state.connector_logs = None

    # Built here rather than in the lifespan because the middleware below closes
    # over it, and middleware is constructed at import time. Reading the manifest
    # is lazy and re-checked per call, so nothing is stale by being early.
    catalogue = build_catalogue(settings.release_manifest_path)
    app.state.releases = catalogue

    # Constructed here, not in the lifespan, because the point of the object is
    # the cache inside it: one per process, shared by every request, so a burst
    # of visitors to the marketing page costs one set of queries a minute.
    app.state.public_stats = PublicStatsService(
        ttl_seconds=settings.public_stats_ttl_seconds,
        online_window_seconds=settings.public_stats_online_window_seconds,
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    # Order matters, and `add_middleware` prepends -- the last one added is the
    # outermost. The intended execution order is:
    #
    #   RequestContext  (an id on every response, including the two below)
    #     -> RateLimit  (so an old client cannot poll the 426 for free)
    #       -> ClientVersion
    #         -> the app
    #
    # so they are added in exactly the reverse of that.
    app.add_middleware(
        ClientVersionMiddleware,
        catalogue=catalogue,
        enforce=settings.enforce_min_app_version,
    )
    app.add_middleware(
        RateLimitMiddleware,
        default_per_minute=settings.rate_limit_per_minute,
        auth_per_minute=settings.auth_rate_limit_per_minute,
    )
    app.add_middleware(RequestContextMiddleware)

    cors_origin_regex = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    if settings.cors_origins or settings.environment == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_origin_regex=cors_origin_regex if settings.environment == "dev" else None,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            # The advisory headers are useless to a browser unless exposed: the
            # web build would otherwise be the one client that never learns an
            # update exists.
            expose_headers=["x-request-id", *ADVISORY_HEADERS],
        )

    app.include_router(api_router, prefix="/v1")
    return app


app = create_app


def run() -> None:
    """Console entry point: ``tally-backend``."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "tally_backend.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container-facing by design
        port=8000,
        reload=settings.debug,
        # WebSockets are long-lived; the default proxy headers config is what
        # lets client IPs survive a load balancer for audit logging.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
