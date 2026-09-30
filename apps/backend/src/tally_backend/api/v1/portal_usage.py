"""The portal's usage view: who is using the product, how much, and on what build.

Same authority as the rest of the portal and the same two rules, applied the
same way:

**Partner scoping goes through the same helpers.** ``portal._scoped`` and
``portal._account_or_404`` are imported, not reimplemented, for the reason
``portal_logs`` gives: a second copy of "may this person see this account" is a
second place to get it wrong.

**Counts only.** Everything here is read from ``user_activity_days``, sessions
and sign-in times -- how often somebody came back, never what they looked at.
The table cannot hold a figure from anybody's books, which is the only way to
be sure this module can never show one.

The demo organisation is left out of every total. It has no users of its own,
and its company being *read* by new signups is their usage, already counted
against their own accounts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Select, distinct, func, select

from ...db.models import (
    Membership,
    Organisation,
    OrgStatus,
    RefreshToken,
    User,
    UserActivityDay,
    as_utc,
    utc_now,
)
from ...services.usage import business_day
from ..deps import PlatformPrincipalDep, SessionDep
from ..portal_schemas import (
    AccountUsage,
    AccountUsageResponse,
    DeviceSession,
    PersonUsage,
    PlatformUsageResponse,
    UsageDay,
    UsageHeadline,
    VersionShare,
)
from .portal import _account_or_404, _scoped

router = APIRouter(prefix="/portal", tags=["portal"])

Days = Annotated[int, Query(ge=7, le=180)]

#: A live account nobody has touched for this long is worth a phone call.
QUIET_AFTER = timedelta(days=7)

_A = UserActivityDay


def _scoped_orgs(principal: PlatformPrincipalDep) -> Select:
    """The ids of the customer accounts in scope, as a subquery."""
    return _scoped(
        select(Organisation.id).where(Organisation.is_demo.is_(False)),
        principal.partner_scope,
    )


def _window(days: int) -> tuple[date, date]:
    today = business_day(utc_now())
    return today - timedelta(days=days - 1), today


def _fill(start: date, end: date, rows: dict[date, UsageDay]) -> list[UsageDay]:
    """One entry per day, zero where nothing happened.

    A missing day and a quiet day are the same thing here -- nobody used the
    product -- but a chart that skips it draws a line straight across the gap
    and hides exactly the day somebody wanted to see.
    """
    out: list[UsageDay] = []
    day = start
    while day <= end:
        out.append(rows.get(day) or UsageDay(day=day))
        day += timedelta(days=1)
    return out


def _stamp(moment: datetime | None) -> float:
    """A sort key for "most recent", with never-seen sorting before anything."""
    return as_utc(moment).timestamp() if moment is not None else float("-inf")


def _latest(*moments: datetime | None) -> datetime | None:
    present = [as_utc(m) for m in moments if m is not None]
    return max(present) if present else None


async def _tracking_since(session: SessionDep) -> date | None:
    return await session.scalar(select(func.min(_A.day)))


@router.get("/usage", response_model=PlatformUsageResponse)
async def platform_usage(
    principal: PlatformPrincipalDep, session: SessionDep, days: Days = 30
) -> PlatformUsageResponse:
    """Usage across every account the caller may see."""
    start, today = _window(days)
    orgs = _scoped_orgs(principal)
    in_scope = _A.org_id.in_(orgs)

    series_rows = await session.execute(
        select(
            _A.day,
            func.count(distinct(_A.user_id)),
            func.count(distinct(_A.org_id)),
            func.sum(_A.events),
            func.sum(_A.dashboard_views),
            func.sum(_A.report_views),
            func.sum(_A.entries_created),
        )
        .where(in_scope, _A.day >= start)
        .group_by(_A.day)
    )
    by_day = {
        day: UsageDay(
            day=day,
            active_users=users,
            active_accounts=accounts,
            events=events or 0,
            dashboard_views=dashboards or 0,
            report_views=reports or 0,
            entries_created=entries or 0,
        )
        for day, users, accounts, events, dashboards, reports, entries in series_rows.all()
    }

    async def active_since(since: date) -> int:
        return (
            await session.scalar(
                select(func.count(distinct(_A.user_id))).where(in_scope, _A.day >= since)
            )
        ) or 0

    totals = (
        await session.execute(
            select(
                func.sum(_A.report_views), func.sum(_A.entries_created), func.sum(_A.logins)
            ).where(in_scope, _A.day >= start)
        )
    ).one()
    people = (
        await session.scalar(
            select(func.count(distinct(Membership.user_id))).where(Membership.org_id.in_(orgs))
        )
    ) or 0

    headline = UsageHeadline(
        active_today=await active_since(today),
        active_7d=await active_since(today - timedelta(days=6)),
        active_30d=await active_since(today - timedelta(days=29)),
        people=people,
        report_views=totals[0] or 0,
        entries_created=totals[1] or 0,
        logins=totals[2] or 0,
        tracking_since=await _tracking_since(session),
    )

    accounts = await _account_usage(session, orgs, start)
    top = sorted(
        (a for a in accounts if a.events),
        key=lambda a: (a.events, a.active_users),
        reverse=True,
    )[:15]

    # Only live accounts: a suspended or pending one going quiet is expected,
    # and a list padded with them hides the account that is actually drifting.
    cutoff = utc_now() - QUIET_AFTER
    quiet = sorted(
        (
            a
            for a in accounts
            if a.status is OrgStatus.ACTIVE
            and (a.last_seen_at is None or a.last_seen_at < cutoff)
        ),
        # Never-seen first, then longest silent.
        key=lambda a: _stamp(a.last_seen_at),
    )[:15]

    return PlatformUsageResponse(
        days=days,
        headline=headline,
        series=_fill(start, today, by_day),
        top_accounts=top,
        quiet_accounts=quiet,
        versions=await _versions(session, in_scope, today - timedelta(days=6)),
    )


async def _account_usage(
    session: SessionDep, orgs: Select, start: date
) -> list[AccountUsage]:
    """Every account in scope, with its window totals and last sign of life.

    Four grouped queries whatever the number of accounts, for the reason
    ``portal._account_responses`` gives: one per row is a page that gets slower
    with every customer.
    """
    rows = (
        await session.execute(
            select(Organisation.id, Organisation.name, Organisation.status).where(
                Organisation.id.in_(orgs)
            )
        )
    ).all()
    if not rows:
        return []

    window = {
        org_id: (users, active_days, events, reports, entries, last_at)
        for org_id, users, active_days, events, reports, entries, last_at in (
            await session.execute(
                select(
                    _A.org_id,
                    func.count(distinct(_A.user_id)),
                    func.count(distinct(_A.day)),
                    func.sum(_A.events),
                    func.sum(_A.report_views),
                    func.sum(_A.entries_created),
                    func.max(_A.last_at),
                )
                .where(_A.org_id.in_(orgs), _A.day >= start)
                .group_by(_A.org_id)
            )
        ).all()
    }
    people = dict(
        (
            await session.execute(
                select(Membership.org_id, func.count(Membership.user_id))
                .where(Membership.org_id.in_(orgs))
                .group_by(Membership.org_id)
            )
        ).all()
    )
    ever = dict(
        (
            await session.execute(
                select(_A.org_id, func.max(_A.last_at))
                .where(_A.org_id.in_(orgs))
                .group_by(_A.org_id)
            )
        ).all()
    )
    logins = dict(
        (
            await session.execute(
                select(Membership.org_id, func.max(User.last_login_at))
                .join(User, User.id == Membership.user_id)
                .where(Membership.org_id.in_(orgs))
                .group_by(Membership.org_id)
            )
        ).all()
    )
    renewals = dict(
        (
            await session.execute(
                select(Membership.org_id, func.max(RefreshToken.last_used_at))
                .join(RefreshToken, RefreshToken.user_id == Membership.user_id)
                .where(Membership.org_id.in_(orgs))
                .group_by(Membership.org_id)
            )
        ).all()
    )

    out: list[AccountUsage] = []
    for org_id, name, org_status in rows:
        users, active_days, events, reports, entries, last_at = window.get(
            org_id, (0, 0, 0, 0, 0, None)
        )
        out.append(
            AccountUsage(
                id=org_id,
                name=name,
                status=org_status,
                people=people.get(org_id, 0),
                active_users=users or 0,
                active_days=active_days or 0,
                events=events or 0,
                report_views=reports or 0,
                entries_created=entries or 0,
                last_seen_at=_latest(
                    last_at, ever.get(org_id), logins.get(org_id), renewals.get(org_id)
                ),
            )
        )
    return out


async def _versions(session: SessionDep, in_scope, since: date) -> list[VersionShare]:  # noqa: ANN001
    """Which build each recently active person used last.

    Grouped by (person, build) in SQL and resolved to "latest" here, so the
    rows returned grow with people and builds rather than with days.
    """
    rows = (
        await session.execute(
            select(_A.user_id, _A.app_version, _A.platform, func.max(_A.day))
            .where(in_scope, _A.day >= since, _A.app_version.is_not(None))
            .group_by(_A.user_id, _A.app_version, _A.platform)
        )
    ).all()
    latest: dict[str, tuple[date, str, str | None]] = {}
    for user_id, version, platform, day in rows:
        if user_id not in latest or day > latest[user_id][0]:
            latest[user_id] = (day, version, platform)

    counts: dict[tuple[str, str | None], int] = {}
    for _, version, platform in latest.values():
        counts[(version, platform)] = counts.get((version, platform), 0) + 1
    return sorted(
        (VersionShare(version=v, platform=p, users=n) for (v, p), n in counts.items()),
        key=lambda share: share.users,
        reverse=True,
    )


@router.get("/accounts/{org_id}/usage", response_model=AccountUsageResponse)
async def account_usage(
    org_id: str, principal: PlatformPrincipalDep, session: SessionDep, days: Days = 30
) -> AccountUsageResponse:
    """One account, person by person."""
    org = await _account_or_404(session, principal, org_id)
    start, today = _window(days)

    members = (
        await session.execute(
            select(Membership.role, Membership.created_at, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == org.id)
            .order_by(Membership.created_at)
        )
    ).all()

    activity = (
        await session.execute(
            select(_A).where(_A.org_id == org.id, _A.day >= start).order_by(_A.day)
        )
    ).scalars().all()

    user_ids = [user.id for _, _, user in members]
    last_ever = dict(
        (
            await session.execute(
                select(_A.user_id, func.max(_A.last_at))
                .where(_A.user_id.in_(user_ids))
                .group_by(_A.user_id)
            )
        ).all()
    ) if user_ids else {}

    now = utc_now()
    tokens = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.user_id.in_(user_ids),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
    ).scalars().all() if user_ids else []

    # One device per token family: rotation leaves a chain of rows behind one
    # sign-in, and listing each would show one phone as a dozen.
    devices: dict[str, dict[str, RefreshToken]] = {}
    for token in tokens:
        family = devices.setdefault(token.user_id, {})
        held = family.get(token.family_id)
        if held is None or as_utc(token.created_at) > as_utc(held.created_at):
            family[token.family_id] = token

    offsets = {start + timedelta(days=i): i for i in range(days)}
    per_person: dict[str, PersonUsage] = {}
    for _role, joined_at, user in members:
        person = PersonUsage(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=str(_role),
            is_active=user.is_active,
            joined_at=joined_at,
            last_login_at=user.last_login_at,
            daily=[0] * days,
        )
        held = devices.get(user.id, {})
        person.devices = sorted(
            (
                DeviceSession(
                    device_name=t.device_name,
                    user_agent=t.user_agent,
                    signed_in_at=t.created_at,
                    last_used_at=t.last_used_at,
                )
                for t in held.values()
            ),
            key=lambda d: _stamp(d.last_used_at or d.signed_in_at),
            reverse=True,
        )
        person.last_seen_at = _latest(
            last_ever.get(user.id),
            user.last_login_at,
            *(t.last_used_at for t in held.values()),
        )
        per_person[user.id] = person

    by_day: dict[date, UsageDay] = {}
    for row in activity:
        day = by_day.setdefault(row.day, UsageDay(day=row.day))
        day.active_users += 1
        day.events += row.events
        day.dashboard_views += row.dashboard_views
        day.report_views += row.report_views
        day.entries_created += row.entries_created

        person = per_person.get(row.user_id)
        if person is None:
            # Somebody who has since left the account. Their days still count
            # towards the account's own series above, which is what happened.
            continue
        person.active_days += 1
        person.events += row.events
        person.dashboard_views += row.dashboard_views
        person.report_views += row.report_views
        person.entries_created += row.entries_created
        person.logins += row.logins
        if row.app_version:
            person.app_version = row.app_version
            person.platform = row.platform
        if row.day in offsets:
            person.daily[offsets[row.day]] = row.events

    people = sorted(
        per_person.values(),
        key=lambda p: (p.events, _stamp(p.last_seen_at)),
        reverse=True,
    )
    return AccountUsageResponse(
        days=days,
        series=_fill(start, today, by_day),
        people=people,
        tracking_since=await _tracking_since(session),
    )

