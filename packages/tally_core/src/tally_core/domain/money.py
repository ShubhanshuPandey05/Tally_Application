"""Monetary value object.

Tally reports amounts as signed decimal strings where the sign encodes the
accounting side: negative is Credit, positive is Debit. Carrying that convention
raw into the UI is how "why is my sales figure negative?" bugs happen, so the
domain layer stores an unsigned magnitude plus an explicit :class:`Side`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

TWO_PLACES = Decimal("0.01")


class Side(StrEnum):
    """Accounting side of an amount."""

    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def sign(self) -> int:
        return 1 if self is Side.DEBIT else -1

    def flipped(self) -> Side:
        return Side.CREDIT if self is Side.DEBIT else Side.DEBIT


class Money(BaseModel):
    """An unsigned amount with an explicit accounting side.

    ``amount`` is always >= 0. Use :attr:`signed` when a single number is needed
    (charts, sums) and :attr:`side` when the caller needs to render Dr/Cr.
    """

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    side: Side = Side.DEBIT
    currency: str = "INR"

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Money.amount must be unsigned; encode direction in `side`")
        return v.quantize(TWO_PLACES)

    @classmethod
    def zero(cls, currency: str = "INR") -> Money:
        return cls(amount=Decimal("0.00"), side=Side.DEBIT, currency=currency)

    @classmethod
    def from_tally(cls, raw: Any, currency: str = "INR") -> Money:
        """Parse a raw Tally amount.

        **Sign convention: in Tally's XML a negative number is a DEBIT and a
        positive number is a CREDIT.** That is the opposite of the usual
        debit-positive convention and is very easy to get backwards. Verified
        against a live TallyPrime on 2026-07-23 across a full chart of accounts:
        Cash-in-Hand ``-344220`` (an asset, so Dr), Capital ``+700000`` (Cr),
        Sundry Creditors ``+212600`` (Cr), Sales ``+114000`` (Cr), Purchases
        ``-197000`` (Dr), Fixed Assets ``-8800`` (Dr) -- and cross-checked against
        ``ISDEEMEDPOSITIVE`` on voucher lines, where the entry carrying
        ``AMOUNT=-700000.00`` is flagged ``ISDEEMEDPOSITIVE=Yes`` (debit).

        An explicit ``Dr``/``Cr`` suffix, which Tally emits in report-style
        exports, always overrides the sign.

        Accepts the shapes Tally actually emits: ``"-12345.67"``, ``"1,234.56"``,
        ``"12345.67 Dr"``, ``"80.00/KG"``, ``""`` and ``None``. Anything
        unparseable becomes zero rather than raising -- a malformed amount in one
        row of a 5,000-row daybook must not fail the whole report.
        """
        if raw is None:
            return cls.zero(currency)

        text = str(raw).strip()
        if not text:
            return cls.zero(currency)

        # Rates arrive as "80.00/KG". The unit is carried separately in the
        # domain model, so drop everything from the slash onward -- otherwise
        # Decimal() rejects the whole string and the rate silently reads zero.
        if "/" in text:
            text = text.split("/", 1)[0].strip()

        explicit_side: Side | None = None
        upper = text.upper()
        for suffix, side in (("DR", Side.DEBIT), ("CR", Side.CREDIT)):
            if upper.endswith(suffix):
                explicit_side = side
                text = text[: -len(suffix)].strip()
                break

        text = text.replace(",", "").replace(" ", "").strip()
        if not text:
            return cls.zero(currency)

        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            return cls.zero(currency)

        side = explicit_side if explicit_side is not None else (
            Side.DEBIT if value < 0 else Side.CREDIT
        )
        return cls(amount=abs(value), side=side, currency=currency)

    @property
    def signed(self) -> Decimal:
        """Debit-positive signed value, for arithmetic and charting."""
        return self.amount * self.side.sign

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def negated(self) -> Money:
        """Same magnitude on the opposite side."""
        if self.is_zero:
            return self
        return Money(amount=self.amount, side=self.side.flipped(), currency=self.currency)

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"cannot add {self.currency} to {other.currency}")
        total = self.signed + other.signed
        return Money(
            amount=abs(total),
            side=Side.CREDIT if total < 0 else Side.DEBIT,
            currency=self.currency,
        )

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {'Dr' if self.side is Side.DEBIT else 'Cr'}"
