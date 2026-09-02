"""Audit trail.

CLAUDE.md requires auditing every request. In a read-only product the sensitive
act *is* the read, so views are recorded as well as changes -- "who looked at the
receivables ledger" is exactly the question an owner will ask after a dispute.

Writes go through the caller's existing session and are committed with it. That
keeps the audit row consistent with the action it describes: an action that rolls
back leaves no misleading log entry claiming it happened.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog

logger = logging.getLogger(__name__)


def client_ip(request: Request | None) -> str | None:
    """Caller's IP, honouring one proxy hop.

    Only the first entry of ``X-Forwarded-For`` is used and it is trusted only
    because the deployment terminates TLS at a load balancer that overwrites the
    header. Behind an untrusted proxy this value is attacker-controlled.
    """
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def record(
    session: AsyncSession,
    *,
    action: str,
    org_id: str | None = None,
    user_id: str | None = None,
    company_id: str | None = None,
    detail: dict[str, Any] | None = None,
    outcome: str = "ok",
    duration_ms: int = 0,
    request: Request | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            org_id=org_id,
            user_id=user_id,
            company_id=company_id,
            detail=detail,
            outcome=outcome,
            duration_ms=duration_ms,
            ip_address=client_ip(request),
        )
    )


async def purge(
    session: AsyncSession,
    *,
    org_ids: list[str] | None,
    before: datetime | None = None,
) -> int:
    """Delete audit rows, scoped the way reading them is.

    Deliberately not committed with a caller's work the way :func:`record` is.
    Clearing a trail is its own act, not a side effect of another one, and it
    should survive or fail on its own.

    ``org_ids`` of ``None`` means unrestricted -- portal owners only. Rows with
    no ``org_id`` at all (a partner being created, say) belong to the platform
    rather than to any account, so a scoped purge leaves them alone: they are
    not the caller's to clear.
    """
    if org_ids is not None and not org_ids:
        return 0

    query = delete(AuditLog)
    if org_ids is not None:
        query = query.where(AuditLog.org_id.in_(org_ids))
    if before is not None:
        query = query.where(AuditLog.created_at < before)

    result = await session.execute(query)
    await session.commit()
    return int(result.rowcount or 0)
