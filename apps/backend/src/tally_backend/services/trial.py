"""The free trial, the phone number that unlocks it, and partner codes.

Until this existed, every signup waited in the portal for somebody to approve
it (see ``services.entitlements``). That is still how a business becomes a
paying customer, but it is no longer how one *starts*: a business whose admin
has given an email and a phone number is activated at once, on a trial with
small fixed limits, and somebody in the portal approves it into a real plan
later.

Three decisions shape this module:

**The trial is granted once per business, ever.** ``trial_granted_at`` is set
when it is given and never cleared. Signing in again, changing the phone
number, or being suspended and reinstated does not hand out a second one.

**The clock starts at the first company of their own, not at signup.** A new
account is lent the demo company and nothing else, so days counted from signup
would be spent looking at invented books -- and a shop owner who signs up on
Saturday and gets to the shop PC on Wednesday would find half the trial gone.
:func:`start_trial_clock` runs where a company is linked, which is the moment
the customer's own figures start to arrive.

**A partner code is a lookup, never a guess.** It is set by an owner when the
partner is created and never changes, so the code on a partner's leaflet keeps
attributing signups for as long as the leaflet exists. An unknown code is
refused out loud rather than ignored: a signup that silently lost its partner
is an account the partner who brought it in can never see.
"""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core.errors import ConflictError, InvalidInput
from ..db.models import Organisation, OrgStatus, PlatformUser, User, utc_now

# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------

#: Indian mobile numbers are ten digits starting 6-9. Landlines are not
#: accepted: the number is how a partner reaches the owner, and a shop's
#: landline is answered by whoever is at the counter.
_INDIAN_MOBILE = re.compile(r"[6-9]\d{9}")
#: E.164 allows up to 15 digits after the country code's ``+``.
_INTERNATIONAL = re.compile(r"\+[1-9]\d{7,14}")


def normalise_phone(raw: str | None) -> str | None:
    """``+<country><number>``, or ``None`` when ``raw`` is not a mobile number.

    Normalised before it is stored *and* before it is compared, so that
    "98765 43210", "098765-43210" and "+91 98765 43210" are one number. Stored
    as typed, the uniqueness check would let the same phone onto three
    accounts by spacing it three ways.

    A bare ten-digit number is read as Indian, because every customer of this
    product keeps their books in TallyPrime in India; anyone else types the
    ``+``.
    """
    if raw is None:
        return None
    compact = re.sub(r"[\s\-().]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]

    if compact.startswith("+"):
        if compact.startswith("+91"):
            rest = compact[3:]
            return f"+91{rest}" if _INDIAN_MOBILE.fullmatch(rest) else None
        return compact if _INTERNATIONAL.fullmatch(compact) else None

    if _INDIAN_MOBILE.fullmatch(compact):
        return f"+91{compact}"
    # A trunk prefix ("098765...") or the country code without its plus.
    if compact.startswith("0") and _INDIAN_MOBILE.fullmatch(compact[1:]):
        return f"+91{compact[1:]}"
    if compact.startswith("91") and _INDIAN_MOBILE.fullmatch(compact[2:]):
        return f"+{compact}"
    return None


def require_phone(raw: str) -> str:
    """:func:`normalise_phone`, refusing anything it cannot read."""
    phone = normalise_phone(raw)
    if phone is None:
        raise InvalidInput(
            f"unreadable phone number {raw!r}",
            user_message="Enter a valid mobile number, like 98765 43210.",
            detail={"field": "phone"},
        )
    return phone


async def ensure_phone_free(
    session: AsyncSession, phone: str, *, user_id: str | None = None
) -> None:
    """Refuse a number that already belongs to somebody else's account."""
    query = select(User.id).where(User.phone == phone)
    if user_id is not None:
        query = query.where(User.id != user_id)
    if await session.scalar(query) is not None:
        raise ConflictError(
            "phone number already in use",
            user_message=(
                "That mobile number is already on another Prime Analyst account. "
                "Sign in to that account, or use a different number."
            ),
            detail={"field": "phone"},
        )


# --------------------------------------------------------------------------
# Partner codes
# --------------------------------------------------------------------------

#: Letters, digits and hyphens, 4 to 20 long, starting and ending on a letter
#: or digit. Short enough to read out over the phone, and no characters that
#: survive being printed on a card badly.
_PARTNER_CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{2,18}[A-Z0-9]")


def normalise_partner_code(raw: str | None) -> str | None:
    """Upper case and trimmed, or ``None`` for an empty box.

    Upper case because a code is read off a card and typed on a phone keyboard
    that capitalises whatever it likes; "ravi-01" and "RAVI-01" are one code.
    """
    if raw is None:
        return None
    code = raw.strip().upper()
    return code or None


def is_well_formed_partner_code(code: str) -> bool:
    return _PARTNER_CODE.fullmatch(code) is not None


async def find_partner_by_code(session: AsyncSession, raw: str | None) -> PlatformUser | None:
    """The active portal account a code belongs to, or ``None``.

    A deactivated partner's code no longer attributes anything: the account it
    would hand them is one they could no longer sign in to look after.
    """
    code = normalise_partner_code(raw)
    if code is None or not is_well_formed_partner_code(code):
        return None
    return await session.scalar(
        select(PlatformUser).where(
            PlatformUser.partner_code == code, PlatformUser.is_active.is_(True)
        )
    )


async def resolve_partner_code(session: AsyncSession, raw: str | None) -> PlatformUser | None:
    """The partner a typed code names; ``None`` for an empty box; refused otherwise.

    The app checks a code as it is typed, but the server decides: an invalid
    code reaching here is refused rather than dropped, so a signup never
    completes believing it is attributed when it is not.
    """
    if normalise_partner_code(raw) is None:
        return None
    partner = await find_partner_by_code(session, raw)
    if partner is None:
        raise InvalidInput(
            f"unknown partner code {raw!r}",
            user_message=(
                "That partner code is not valid. Check it with your partner, "
                "or leave the box empty."
            ),
            detail={"field": "partner_code"},
        )
    return partner


# --------------------------------------------------------------------------
# The trial itself
# --------------------------------------------------------------------------


def is_on_trial(org: Organisation) -> bool:
    """Running on the free trial, rather than on a plan somebody approved.

    Derived from two timestamps instead of stored as a flag: approving an
    account sets ``approved_at``, which ends the trial without a second column
    that could disagree with it.
    """
    return org.trial_granted_at is not None and org.approved_at is None


def grant_trial_if_eligible(org: Organisation, user: User, settings: Settings) -> bool:
    """Activate a waiting business on the trial. True when it did.

    Only a ``PENDING`` business that has never had a trial, and only on the
    word of somebody with a phone number. Every other state is somebody's
    decision already -- a rejected signup must not come back to life by
    signing in again, and a suspended one must not get a fresh week.

    Called at signup, at every sign-in and when a phone number is added, so an
    account made before trials existed gets one the moment its admin supplies
    the number the app now asks for.
    """
    if not settings.trial_enabled:
        return False
    if org.is_demo or org.status is not OrgStatus.PENDING:
        return False
    if org.trial_granted_at is not None or org.approved_at is not None:
        return False
    if not user.phone:
        return False

    org.status = OrgStatus.ACTIVE
    org.max_companies = settings.trial_max_companies
    org.max_users = settings.trial_max_users
    # Open-ended until the clock starts. Expiry is how a trial ends -- the same
    # computed check every other term uses -- so nothing has to sweep the table.
    org.expires_at = None
    org.trial_granted_at = utc_now()
    return True


def start_trial_clock(org: Organisation, settings: Settings) -> bool:
    """Start counting the trial's days. True when this call started it.

    Runs where a company is linked, after the limit check has passed. Never
    restarts: unlinking the company and linking it again, or linking a second
    one, leaves the original start where it was.
    """
    if not is_on_trial(org) or org.trial_started_at is not None:
        return False
    now = utc_now()
    org.trial_started_at = now
    org.expires_at = now + timedelta(days=settings.trial_days)
    return True
