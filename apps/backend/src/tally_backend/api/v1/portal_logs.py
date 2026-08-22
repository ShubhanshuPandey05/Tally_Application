"""The portal's support view: backend logs, connector logs, activity.

Split out of :mod:`.portal` because it answers a different question. That module
decides what a business is entitled to; this one exists for the ten minutes
after a customer rings up saying "it stopped working". Same authority, same
router prefix, same partner scoping -- different job.

Three rules carry over unchanged, and one is new.

**Partner scoping goes through the same helper.** ``portal._account_or_404`` is
imported rather than reimplemented. A second copy of "may this person see this
account?" is a second copy to forget to update, and the whole reason that helper
exists is that the rule should live in exactly one place.

**Still no financial figures.** A connector's log says that a sync ran and how
long it took. It does not say what was in it, and nothing here reads a
customer's data. That boundary is the same one ``portal.py`` documents.

**Backend logs are owner-only.** A partner is scoped to their own accounts, and
the server log has no ``org_id`` to scope by -- a stack trace from one tenant's
request routinely names another's connector. Rather than attempt to filter that,
the whole surface requires ``PlatformRole.OWNER``.

**Streaming reads the ring, never the database.** A live tail that polled
Postgres every second would be a support tool whose cost scales with how worried
somebody is.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ...db.models import (
    AuditLog,
    Connector,
    ConnectorStatus,
    Organisation,
    PlatformRole,
    PlatformUser,
    User,
    as_utc,
)
from ...services.connector_logs import connector_logs, log_activity
from ...services.logs import stored_server_logs
from ..deps import HubDep, PlatformPrincipal, PlatformPrincipalDep, SessionDep
from ..portal_schemas import AuditEntry, ConnectorSummary, LogLine, LogPage
from .portal import _account_or_404, _scoped

router = APIRouter(prefix="/portal", tags=["portal"])

#: Sent every fifteen seconds on an idle stream. A proxy that sees no bytes
#: closes the connection as idle, and a support tail that dies silently after a
#: quiet minute is worse than no tail -- the reader believes nothing happened.
_KEEPALIVE_SECONDS = 15.0


# --------------------------------------------------------------------------
# Backend logs
# --------------------------------------------------------------------------


@router.get("/logs/backend", response_model=LogPage)
async def backend_logs(
    principal: PlatformPrincipalDep,
    session: SessionDep,
    request: Request,
    source: Annotated[str, Query(pattern="^(live|stored)$")] = "live",
    level: Annotated[str, Query(max_length=10)] = "",
    logger_name: Annotated[str, Query(alias="logger", max_length=120)] = "",
    search: Annotated[str, Query(alias="q", max_length=200)] = "",
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> LogPage:
    """What this backend process is doing, or what it wrote down before.

    ``live`` reads the in-memory ring: every level, this process only, gone on
    restart. ``stored`` reads ``server_logs``: WARNING and above, every
    instance, surviving a redeploy. The two are offered as a switch rather than
    merged because "I cannot find it" has completely different answers depending
    on which one is being read, and a merged view hides which that was.
    """
    principal.require(PlatformRole.OWNER)
    store = request.app.state.log_store

    if source == "stored":
        views = await stored_server_logs(
            session, level=level, logger_name=logger_name, search=search, limit=limit
        )
    else:
        views = store.recent(
            level=level,
            logger_name=logger_name,
            search=search,
            limit=limit,
            after_seq=after_seq,
        )

    return LogPage(
        lines=[
            LogLine(
                seq=view.seq,
                created_at=view.created_at,
                level=view.level,
                logger=view.logger,
                message=view.message,
                instance_id=view.instance_id or None,
                request_id=view.request_id,
                traceback=view.traceback,
            )
            for view in views
        ],
        latest_seq=store.last_seq,
        source=source,
        instance_id=request.app.state.settings.instance_id,
    )


@router.get("/logs/backend/stream")
async def stream_backend_logs(
    principal: PlatformPrincipalDep,
    request: Request,
    level: Annotated[str, Query(max_length=10)] = "",
    logger_name: Annotated[str, Query(alias="logger", max_length=120)] = "",
    search: Annotated[str, Query(alias="q", max_length=200)] = "",
) -> StreamingResponse:
    """Server-sent events: every captured line, as it happens.

    Not an ``EventSource`` on the client side, deliberately. ``EventSource``
    cannot set an ``Authorization`` header, so using it would mean putting a
    portal token in a query string -- where it lands in every proxy access log
    on the way. The portal reads this with ``fetch`` and a streaming body
    instead, which keeps the token in a header for the same wire format.
    """
    principal.require(PlatformRole.OWNER)
    store = request.app.state.log_store

    async def events() -> AsyncIterator[bytes]:
        with store.subscribe() as queue:
            # Announce the resume point first. A client that reconnects can ask
            # /logs/backend for everything after it, so the gap across a dropped
            # stream is recoverable rather than invisible.
            yield _sse({"type": "open", "latest_seq": store.last_seq})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    view = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if not view.matches(level=level, logger_name=logger_name, search=search):
                    continue
                yield _sse({"type": "line", **view.as_dict()})

    return StreamingResponse(
        _guarded(events()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Caddy does not buffer by design, but this deployment is one
            # reverse proxy away from an nginx that does -- and a buffered SSE
            # stream simply never arrives.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()


async def _guarded(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Let a client disconnect end a stream quietly.

    A browser closing a tab surfaces as a cancellation part-way through a yield.
    Without this it is logged as an unhandled error on every single closed tab,
    which turns the log this endpoint exists to serve into noise about itself.
    """
    with contextlib.suppress(asyncio.CancelledError, ConnectionError):
        async for chunk in stream:
            yield chunk


# --------------------------------------------------------------------------
# Connector logs
# --------------------------------------------------------------------------


@router.get("/accounts/{org_id}/connectors", response_model=list[ConnectorSummary])
async def account_connectors(
    org_id: str,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
) -> list[ConnectorSummary]:
    """The customer's Tally PCs, with enough state to pick the interesting one."""
    org = await _account_or_404(session, principal, org_id)
    rows = (
        (
            await session.execute(
                select(Connector)
                .where(Connector.org_id == org.id, Connector.status != ConnectorStatus.REVOKED)
                .order_by(Connector.created_at)
            )
        )
        .scalars()
        .all()
    )
    activity = await log_activity(session, [row.id for row in rows])
    # Asked of the hub, and of the bus behind it, for the reason
    # `portal._online_ids` documents: `last_seen_at` only advances on connect,
    # disconnect and Tally status changes, so a healthy connector's stored
    # timestamp freezes at connect time and the column would report a fleet
    # that goes dark a few minutes after it comes up.
    online = {
        row.id
        for row in rows
        if hub.local_link(row.id) is not None or await hub.is_online(row.id)
    }
    return [_connector_summary(row, org, row.id in online, activity) for row in rows]


@router.get("/logs/connector", response_model=LogPage)
async def connector_log_page(
    principal: PlatformPrincipalDep,
    session: SessionDep,
    org_id: Annotated[str, Query(max_length=32)] = "",
    connector_id: Annotated[str, Query(max_length=32)] = "",
    level: Annotated[str, Query(max_length=10)] = "",
    logger_name: Annotated[str, Query(alias="logger", max_length=120)] = "",
    search: Annotated[str, Query(alias="q", max_length=200)] = "",
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> LogPage:
    """What a customer's connector has been saying, newest first.

    ``before`` pages backwards from the oldest row on screen. Cursor-style
    rather than an offset because rows keep arriving while somebody reads, and
    an offset would quietly shift every page under them.

    Scope is resolved to a concrete list of organisation ids for a partner and
    to ``None`` -- meaning unrestricted -- for an owner. The distinction is
    carried into the query rather than applied to its results, so there is no
    arrangement of filters that returns a row from an account the caller cannot
    see.
    """
    org_ids = await _visible_org_ids(session, principal, org_id)
    rows = await connector_logs(
        session,
        org_ids=org_ids,
        connector_id=connector_id,
        level=level,
        logger_name=logger_name,
        search=search,
        before=before,
        limit=limit,
    )
    names = await _org_names(session, {row.org_id for row in rows})
    return LogPage(
        source="stored",
        lines=[
            LogLine(
                created_at=as_utc(row.created_at),
                logged_at=as_utc(row.logged_at),
                level=row.level,
                logger=row.logger,
                message=row.message,
                connector_id=row.connector_id,
                org_id=row.org_id,
                org_name=names.get(row.org_id),
                session_id=row.session_id or None,
                dropped_before=row.dropped_before,
            )
            for row in rows
        ],
    )


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


@router.get("/audit", response_model=list[AuditEntry])
async def audit_trail(
    principal: PlatformPrincipalDep,
    session: SessionDep,
    org_id: Annotated[str, Query(max_length=32)] = "",
    action: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEntry]:
    """Who did what, newest first.

    The same table the product audits every read into, filtered to what the
    caller may see. Portal actions carry no ``org_id`` when they are not about
    one account -- creating a partner, say -- so an owner sees those and a
    partner does not, which is the correct answer in both directions.
    """
    org_ids = await _visible_org_ids(session, principal, org_id)

    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if org_ids is not None:
        if not org_ids:
            return []
        query = query.where(AuditLog.org_id.in_(org_ids))
    if org_id:
        query = query.where(AuditLog.org_id == org_id)
    if action.strip():
        query = query.where(AuditLog.action.ilike(f"%{action.strip()}%"))

    rows = (await session.execute(query)).scalars().all()
    names = await _org_names(session, {row.org_id for row in rows if row.org_id})
    actors = await _actor_names(session, {row.user_id for row in rows if row.user_id})
    return [
        AuditEntry(
            id=row.id,
            created_at=as_utc(row.created_at),
            action=row.action,
            org_id=row.org_id,
            org_name=names.get(row.org_id or ""),
            user_id=row.user_id,
            actor=actors.get(row.user_id or ""),
            company_id=row.company_id,
            ip_address=row.ip_address,
            outcome=row.outcome,
            duration_ms=row.duration_ms,
            detail=row.detail,
        )
        for row in rows
    ]


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


async def _visible_org_ids(
    session: SessionDep, principal: PlatformPrincipal, org_id: str
) -> list[str] | None:
    """Which accounts this query may touch. ``None`` means "no restriction".

    A named ``org_id`` goes through ``_account_or_404`` -- the same choke point
    the rest of the portal uses -- so asking for somebody else's account answers
    404 rather than an empty page. An empty page would be a slower way of
    telling a partner the account exists.
    """
    if org_id.strip():
        org = await _account_or_404(session, principal, org_id.strip())
        return [org.id]
    if principal.partner_scope is None:
        return None
    rows = await session.execute(_scoped(select(Organisation.id), principal.partner_scope))
    return [row for (row,) in rows.all()]


async def _org_names(session: SessionDep, org_ids: set[str]) -> dict[str, str]:
    if not org_ids:
        return {}
    rows = await session.execute(
        select(Organisation.id, Organisation.name).where(Organisation.id.in_(org_ids))
    )
    return dict(rows.all())


async def _actor_names(session: SessionDep, user_ids: set[str]) -> dict[str, str]:
    """Resolve an audit row's ``user_id`` across both identity tables.

    ``AuditLog.user_id`` holds a tenant ``User`` for a customer's action and a
    ``PlatformUser`` for a portal one, with no column saying which. Two lookups
    is the honest cost of that; ids are random hex so a collision between the
    tables is not a real concern.
    """
    if not user_ids:
        return {}
    names: dict[str, str] = {}
    for model in (User, PlatformUser):
        rows = await session.execute(
            select(model.id, model.full_name, model.email).where(model.id.in_(user_ids))
        )
        for row_id, full_name, email in rows.all():
            names.setdefault(row_id, full_name or email)
    return names


def _connector_summary(
    row: Connector,
    org: Organisation,
    online: bool,
    activity: dict[str, tuple[datetime | None, int]],
) -> ConnectorSummary:
    last_log_at, errors = activity.get(row.id, (None, 0))
    return ConnectorSummary(
        id=row.id,
        org_id=org.id,
        org_name=org.name,
        label=row.name or row.hostname or row.id[:8],
        hostname=row.hostname,
        os=row.os,
        connector_version=row.connector_version,
        status=row.status.value,
        online=online,
        # Only meaningful while `online`. A connector that went offline leaves
        # its last known Tally state behind, and rendering that as "Tally is up"
        # over a machine that is switched off is the kind of wrong answer that
        # sends support to the wrong end of the problem.
        tally_online=online and bool(row.last_tally_online),
        last_seen_at=as_utc(row.last_seen_at) if row.last_seen_at else None,
        last_log_at=last_log_at,
        error_count=errors,
    )
