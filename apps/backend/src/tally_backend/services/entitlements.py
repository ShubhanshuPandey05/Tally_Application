"""What one organisation is allowed to do, and how much of it.

This replaces ``services.limits``, which asked only "how many?". The question
turned out to have a gate in front of it: **a signup provisions nothing.** A
shop owner can install the app, register, and sign in — and then finds there is
nothing to add until someone in the management portal approves the account and
says how many people and how many companies it covers.

Two separate permissions come out of that, and conflating them is the mistake
this module exists to prevent:

``allows_changes``
    May this account grow — add a Tally PC, link a company, add a colleague?
    Requires a live subscription. A pending account has none yet; a suspended
    one had one.

``allows_data``
    May this account read its books at all? A *pending* account may: there is
    nothing there to read, and refusing would turn the first launch after signup
    into an error screen rather than "we are setting you up". A *suspended* one
    may not — a suspension that still served every report would be a suspension
    in name only.

Both are computed from the organisation row and nothing else, so there is no
second place for the answer to come from and drift.

Counting is done against the database rather than a cached counter. A counter
that drifts hands out free seats, and reconciling it is a background job nobody
writes. Two indexed ``COUNT(*)`` queries on a path that runs a handful of times
per customer, ever, is not a cost worth optimising.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import ConflictError, SubscriptionInactive
from ..db.models import Company, Membership, Organisation, OrgStatus, as_utc, utc_now


@dataclass(frozen=True)
class Entitlement:
    """The commercial state of one organisation, decided in one place."""

    status: OrgStatus
    max_users: int
    max_companies: int
    expires_at: datetime | None = None
    #: The shared demo account. Live enough to read, frozen against every
    #: change -- see :attr:`allows_changes`.
    is_demo: bool = False

    @classmethod
    def of(cls, org: Organisation) -> Entitlement:
        return cls(
            status=org.status,
            max_users=org.max_users,
            max_companies=org.max_companies,
            expires_at=org.expires_at,
            is_demo=org.is_demo,
        )

    @property
    def is_expired(self) -> bool:
        """Past the agreed term.

        Kept as a computed property rather than a fifth :class:`OrgStatus`, so
        nothing has to sweep the table at midnight flipping rows. A background
        job that stops running would otherwise leave every expired account
        quietly live, which is the failure nobody notices.
        """
        return self.expires_at is not None and as_utc(self.expires_at) <= utc_now()

    @property
    def allows_changes(self) -> bool:
        # The demo is refused here rather than at each endpoint, for the reason
        # this module exists at all: a guard that has to be remembered beside
        # every mutation is one a later mutation ships without. Anyone can sign
        # into the demo, so "anyone" would otherwise be able to unlink its
        # company or revoke its connector for everybody else looking at it.
        if self.is_demo:
            return False
        return self.status is OrgStatus.ACTIVE and not self.is_expired

    @property
    def allows_data(self) -> bool:
        # Pending is included deliberately -- see the module docstring.
        return self.status in {OrgStatus.PENDING, OrgStatus.ACTIVE} and not self.is_expired

    @property
    def blocked_reason(self) -> str:
        """Why an account cannot grow, written for the shop owner.

        Every one of these names the way *out*. "Not activated" with no next step
        is the message that generates the support call this text is meant to
        replace — and there is no self-service path here by design, so the way
        out is always a person.
        """
        if self.is_demo:
            return (
                "This is the TallyFlow demo, so nothing here can be changed. "
                "Create your own account to connect your TallyPrime."
            )
        if self.is_expired and self.status is OrgStatus.ACTIVE:
            return (
                "Your TallyFlow subscription has ended. Contact your TallyFlow "
                "partner to renew it."
            )
        return {
            OrgStatus.PENDING: (
                "Your business is waiting to be approved. You can sign in now, "
                "and adding a Tally PC, a company or a colleague unlocks as soon "
                "as your TallyFlow partner activates the account."
            ),
            OrgStatus.SUSPENDED: (
                "This TallyFlow account has been suspended. Contact your "
                "TallyFlow partner to restore it."
            ),
            OrgStatus.REJECTED: (
                "This TallyFlow account was not activated. Contact your TallyFlow "
                "partner if you think that is a mistake."
            ),
            OrgStatus.ACTIVE: "",
        }[self.status]


def require_changes(org: Organisation) -> None:
    """Gate every path that makes an organisation bigger."""
    entitlement = Entitlement.of(org)
    if entitlement.allows_changes:
        return
    raise SubscriptionInactive(
        f"org {org.id} is {entitlement.status} (expired={entitlement.is_expired})",
        user_message=entitlement.blocked_reason,
        detail={"org_status": entitlement.status.value, "expired": entitlement.is_expired},
    )


def require_mutable(org: Organisation) -> None:
    """Gate a change to what an organisation already *holds*.

    Deliberately not :func:`require_changes`. That one gates growth, and growth
    is a commercial question: a lapsed customer may not add a third company.
    Taking one away is not the same question -- a customer whose subscription
    ended is still entitled to unlink their books and unpair their PC, and
    refusing that would be punitive rather than commercial.

    The demo is the case this exists for. Anyone may sign into it, so nobody
    may take it apart: one visitor unlinking the company would empty the
    showroom for everybody else looking at it.
    """
    entitlement = Entitlement.of(org)
    if not entitlement.is_demo:
        return
    raise SubscriptionInactive(
        f"org {org.id} is the shared demo",
        user_message=entitlement.blocked_reason,
        detail={"is_demo": True},
    )


def require_data(org: Organisation) -> None:
    """Gate reading the books. Lenient towards a brand new account."""
    entitlement = Entitlement.of(org)
    if entitlement.allows_data:
        return
    raise SubscriptionInactive(
        f"org {org.id} is {entitlement.status} (expired={entitlement.is_expired})",
        user_message=entitlement.blocked_reason,
        detail={"org_status": entitlement.status.value, "expired": entitlement.is_expired},
    )


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


async def count_companies(session: AsyncSession, org_id: str) -> int:
    """Active companies only.

    A company that was unlinked has released its slot. Charging for something
    the customer deliberately removed, and can no longer see, is the kind of
    billing surprise that loses an account.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.org_id == org_id, Company.is_active.is_(True))
        )
    ) or 0


async def count_users(session: AsyncSession, org_id: str) -> int:
    """Memberships, not users.

    The same accountant helping two businesses is two seats, one in each — which
    is what both of those businesses would expect to be told.
    """
    return (
        await session.scalar(
            select(func.count()).select_from(Membership).where(Membership.org_id == org_id)
        )
    ) or 0


async def check_company_limit(session: AsyncSession, org: Organisation) -> None:
    """Raise if this organisation may not link another company.

    The subscription gate is checked *here* rather than beside every caller, so
    the two can never be separated: a future endpoint that creates a company and
    remembers the count but forgets the status is not a code path that exists.
    """
    require_changes(org)
    current = await count_companies(session, org.id)
    if current >= org.max_companies:
        raise ConflictError(
            f"org {org.id} is at its company limit ({org.max_companies})",
            user_message=(
                f"Your plan covers {org.max_companies} "
                f"{'company' if org.max_companies == 1 else 'companies'}. "
                "Remove one, or contact your TallyFlow partner to add more."
            ),
            detail={"limit": org.max_companies, "used": current},
        )


async def check_user_limit(session: AsyncSession, org: Organisation) -> None:
    """Raise if this organisation may not add another member."""
    require_changes(org)
    current = await count_users(session, org.id)
    if current >= org.max_users:
        raise ConflictError(
            f"org {org.id} is at its user limit ({org.max_users})",
            user_message=(
                f"Your plan covers {org.max_users} "
                f"{'person' if org.max_users == 1 else 'people'}. "
                "Remove someone, or contact your TallyFlow partner to add more."
            ),
            detail={"limit": org.max_users, "used": current},
        )
