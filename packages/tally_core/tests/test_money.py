from __future__ import annotations

from decimal import Decimal

import pytest

from tally_core.domain.money import Money, Side


@pytest.mark.parametrize(
    ("raw", "expected_amount", "expected_side"),
    [
        # Tally's convention, verified against a live instance: a NEGATIVE
        # number is a debit and a POSITIVE number is a credit.
        ("1234.56", Decimal("1234.56"), Side.CREDIT),
        ("-1234.56", Decimal("1234.56"), Side.DEBIT),
        ("2,50,000.00", Decimal("250000.00"), Side.CREDIT),
        ("-2,50,000.00", Decimal("250000.00"), Side.DEBIT),
        # An explicit suffix overrides the sign.
        ("12345.67 Dr", Decimal("12345.67"), Side.DEBIT),
        ("12345.67 Cr", Decimal("12345.67"), Side.CREDIT),
        ("-12345.67 Cr", Decimal("12345.67"), Side.CREDIT),
        # Rates carry a unit suffix.
        ("80.00/KG", Decimal("80.00"), Side.CREDIT),
        ("-150.00/PACKS", Decimal("150.00"), Side.DEBIT),
        ("", Decimal("0.00"), Side.DEBIT),
        (None, Decimal("0.00"), Side.DEBIT),
        ("not-a-number", Decimal("0.00"), Side.DEBIT),
    ],
)
def test_from_tally_parses_the_shapes_tally_emits(raw, expected_amount, expected_side):
    money = Money.from_tally(raw)
    assert money.amount == expected_amount
    assert money.side is expected_side


def test_negative_is_debit_not_credit():
    """Regression guard for the inverted-sign bug found on first live contact.

    Reading Tally's negatives as credits put cash, banks and every other asset
    on the wrong side -- the dashboard would have shown a healthy bank balance
    as an overdraft.
    """
    assert Money.from_tally("-344220.00").side is Side.DEBIT
    assert Money.from_tally("700000.00").side is Side.CREDIT


def test_explicit_suffix_beats_sign():
    """A trailing Cr is authoritative even when the number is unsigned."""
    assert Money.from_tally("500 Cr").side is Side.CREDIT


def test_signed_is_debit_positive():
    """`signed` re-normalises to the conventional debit-positive form."""
    assert Money.from_tally("-100").signed == Decimal("100.00")  # debit
    assert Money.from_tally("100").signed == Decimal("-100.00")  # credit


def test_amount_must_be_unsigned():
    with pytest.raises(ValueError, match="unsigned"):
        Money(amount=Decimal("-5"), side=Side.DEBIT)


def test_addition_crosses_zero_and_flips_side():
    # -100 is a 100 debit; +250 is a 250 credit. Net: 150 credit.
    total = Money.from_tally("-100") + Money.from_tally("250")
    assert total.side is Side.CREDIT
    assert total.amount == Decimal("150.00")


def test_addition_rejects_mixed_currency():
    with pytest.raises(ValueError, match="cannot add"):
        Money.from_tally("100") + Money(amount=Decimal("1"), currency="USD")


def test_negated_zero_stays_debit():
    """Zero has no meaningful side; flipping it would render as '0.00 Cr'."""
    assert Money.zero().negated().side is Side.DEBIT
