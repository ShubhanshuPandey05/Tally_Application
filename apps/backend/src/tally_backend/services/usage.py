"""Per-person usage: folded from the audit trail as it is written, read by the portal.

The audit trail answers "what happened on this request?" and is kept for two
days. This answers "who is using the product, how much, and on which build?"
over months -- the question a partner has when an account renews, and the one
support has when somebody says "it doesn't work" and has not opened the app in
three weeks.

Written in the same place and the same transaction as the audit row, rather
than rolled up from the trail later. A rollup needs a watermark, and a
watermark that falls behind the trail's two-day pruning loses days silently;
an upsert beside the audit row cannot fall behind anything.

**Counts only.** No report parameter, no company, no figure. The portal reads
this table, and the portal must never be able to show what is in anybody's
books -- so the only safe design is one where the table cannot hold it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.version_middleware import PLATFORM_HEADER, VERSION_HEADER
from ..db.models import UserActivityDay, utc_now

#: India has no daylight saving, so a fixed offset is exact -- and it does not
#: depend on a tz database being installed on whatever box runs this.
BUSINESS_TZ = timezone(timedelta(hours=5, minutes=30), "IST")


def business_day(at: datetime) -> date:
    """The Indian calendar day ``at`` falls on."""
    return at.astimezone(BUSINESS_TZ).date()


@dataclass(frozen=True)
class _Kind:
    dashboard: int = 0
    report: int = 0
    entry: int = 0
    login: int = 0


def _kind_of(action: str) -> _Kind:
    if action == "dashboard.view":
        return _Kind(dashboard=1)
    if action.startswith("report."):
        return _Kind(report=1)
    if action == "voucher.create":
        return _Kind(entry=1)
    # A registration signs its person in and hands them a session, so it is
    # their first login as far as "when did they last come back?" is concerned.
    if action in {"auth.login", "auth.register"}:
        return _Kind(login=1)
    return _Kind()


def _counts_as_use(action: str, user_id: str | None, org_id: str | None) -> bool:
    # `portal.*` rows carry a *portal* user's id, from a different table. Folding
    # them in would credit a customer account with its partner's clicks.
    return bool(user_id and org_id) and not action.startswith("portal.")


async def note(
    session: AsyncSession,
    *,
    action: str,
    org_id: str | None,
    user_id: str | None,
    request: Request | None,
    at: datetime | None = None,
) -> None:
    """Count one audited action towards its person's day.

    An upsert, because two screens opening at once for the same person are two
    concurrent requests that would otherwise race to insert the same row -- and
    the loser's error would fail a read that has nothing to do with usage.
    """
    if not _counts_as_use(action, user_id, org_id):
        return
    assert user_id is not None and org_id is not None

    now = at or utc_now()
    kind = _kind_of(action)
    version = _header(request, VERSION_HEADER, 32)
    platform = _header(request, PLATFORM_HEADER, 16)

    values = {
        "user_id": user_id,
        "day": business_day(now),
        "org_id": org_id,
        "first_at": now,
        "last_at": now,
        "events": 1,
        "dashboard_views": kind.dashboard,
        "report_views": kind.report,
        "entries_created": kind.entry,
        "logins": kind.login,
        "app_version": version,
        "platform": platform,
    }

    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(UserActivityDay).values(**values)
    table = UserActivityDay.__table__.c
    excluded = statement.excluded
    updates = {
        "last_at": excluded.last_at,
        "org_id": excluded.org_id,
        "events": table.events + 1,
        "dashboard_views": table.dashboard_views + kind.dashboard,
        "report_views": table.report_views + kind.report,
        "entries_created": table.entries_created + kind.entry,
        "logins": table.logins + kind.login,
    }
    # Only overwrite the build when this request said which one it was. A call
    # without the headers -- a script, a very old build -- must not blank out
    # the version the app reported a minute earlier.
    if version is not None:
        updates["app_version"] = excluded.app_version
    if platform is not None:
        updates["platform"] = excluded.platform

    await session.execute(
        statement.on_conflict_do_update(index_elements=["user_id", "day"], set_=updates)
    )


def _header(request: Request | None, name: str, limit: int) -> str | None:
    if request is None:
        return None
    value = (request.headers.get(name) or "").strip()
    return value[:limit] or None


async def prune(session: AsyncSession, *, keep_days: int) -> None:
    """Drop days older than the retention window. Not committed here."""
    cutoff = business_day(utc_now()) - timedelta(days=max(keep_days, 0))
    await session.execute(delete(UserActivityDay).where(UserActivityDay.day < cutoff))
