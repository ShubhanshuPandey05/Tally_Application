"""What a business can buy, and the record of every time somebody bought it.

Two plans, each for one year (decided 2026-10-24). A partner or an owner picks
one whenever they activate or renew an account, and that pick is written down
as a :class:`~tally_backend.db.models.PlanSale` -- the row the sales report
counts.

**Money is integer paise, never a float.** The same rule as ``Money`` on the
reporting side, for the same reason: the report adds these up, and a GST total
that is off by a paisa because ``0.18`` cannot be represented is a figure a
partner will be asked to explain to an accountant.

**A sale keeps the price it was sold at.** The catalogue below can change; a
sale already made cannot. Each row carries its own base price, GST and total,
so raising a price next year does not quietly rewrite last year's report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

#: The term every plan is sold for. A year, counted in days rather than by
#: calendar so a plan sold on 29 February has an end date.
PLAN_TERM = timedelta(days=365)

#: Indian GST on software subscriptions. Held as a whole-number percentage so
#: the arithmetic stays in integers.
GST_PERCENT = 18


class PlanCode(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True)
class Plan:
    code: PlanCode
    name: str
    #: Before GST, in paise.
    price_paise: int
    #: What the approval form suggests. The person approving may change both;
    #: the plan decides the price, not the ceilings.
    suggested_users: int
    suggested_companies: int

    @property
    def gst_paise(self) -> int:
        # Both prices are whole rupees, so 18% is exact; integer division is
        # still floor, never a rounding surprise, if a price ever changes.
        return self.price_paise * GST_PERCENT // 100

    @property
    def total_paise(self) -> int:
        return self.price_paise + self.gst_paise


CATALOGUE: dict[PlanCode, Plan] = {
    PlanCode.STANDARD: Plan(
        code=PlanCode.STANDARD,
        name="Standard",
        price_paise=3000_00,
        suggested_users=3,
        suggested_companies=1,
    ),
    PlanCode.PREMIUM: Plan(
        code=PlanCode.PREMIUM,
        name="Premium",
        price_paise=5000_00,
        suggested_users=10,
        suggested_companies=3,
    ),
}


def plan_for(code: PlanCode | str) -> Plan:
    return CATALOGUE[PlanCode(code)]


def term_from(start: datetime) -> datetime:
    return start + PLAN_TERM
