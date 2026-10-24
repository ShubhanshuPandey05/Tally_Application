"""What the receipt, payment and ledger prints need beyond the posted lines.

The receipt print's "Adjustment Details" is the one place on it where a figure
comes from somewhere other than the voucher being printed, so it is the place a
wrong pending amount would come from. These pin it to the arithmetic an
accountant would do by hand: the bill, less everything that settled it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tally_backend.services import analytics as an
from tally_backend.services.bill_adjustments import adjustments

PARTY = "BEST SAYAR MEDICAL CENTER PALI"


def _money(amount: str, side: str) -> dict[str, str]:
    return {"amount": amount, "side": side, "currency": "INR"}


def _voucher(
    kind: str,
    number: str,
    on: date,
    party_amount: dict[str, str],
    other: tuple[str, dict[str, str]],
    bills: list[dict[str, Any]] | None = None,
    bill_references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "voucher_type": kind.replace("_", " ").title(),
        "kind": kind,
        "date": on.isoformat(),
        "voucher_number": number,
        "guid": f"{kind}-{number}",
        "party_name": PARTY,
        "ledger_entries": [
            {
                "ledger_name": PARTY,
                "amount": party_amount,
                "is_party": True,
                "bills": bills or [],
                "bill_references": bill_references
                if bill_references is not None
                else [b["name"] for b in bills or []],
            },
            {"ledger_name": other[0], "amount": other[1]},
        ],
    }


SALE = _voucher(
    "sales",
    "SMA26/002",
    date(2026, 4, 1),
    _money("2100.00", "debit"),
    ("Sales", _money("2100.00", "credit")),
    bills=[
        {
            "name": "SMA26/002",
            "bill_type": "New Ref",
            "bill_date": "2026-04-01",
            "amount": _money("2100.00", "debit"),
            "credit_days": 15,
        }
    ],
)


def _receipt(amount: str, number: str = "1", on: date = date(2026, 4, 5)) -> dict[str, Any]:
    return _voucher(
        "receipt",
        number,
        on,
        _money(amount, "credit"),
        ("ICICI Bank", _money(amount, "debit")),
        bills=[
            {"name": "SMA26/002", "bill_type": "Agst Ref", "amount": _money(amount, "credit")}
        ],
    )


def test_a_bill_settled_in_full_shows_nothing_pending() -> None:
    sale, receipt = an.parse_vouchers([SALE, _receipt("2100.00")])
    (row,) = adjustments(receipt, [sale, receipt])

    assert row["name"] == "SMA26/002"
    assert row["bill_date"] == "2026-04-01"
    assert row["credit_days"] == 15
    assert row["bill_amount"]["amount"] == "2100.00"
    assert row["paid_amount"]["amount"] == "2100.00"
    assert row["pending_amount"]["amount"] == "0.00"
    assert row["note_amount"] is None


def test_pending_counts_earlier_receipts_and_notes() -> None:
    first = _receipt("500.00", number="1", on=date(2026, 4, 3))
    note = _voucher(
        "credit_note",
        "CN-1",
        date(2026, 4, 4),
        _money("100.00", "credit"),
        ("Sales Returns", _money("100.00", "debit")),
        bills=[
            {"name": "SMA26/002", "bill_type": "Agst Ref", "amount": _money("100.00", "credit")}
        ],
    )
    second = _receipt("1000.00", number="2", on=date(2026, 4, 5))
    history = an.parse_vouchers([SALE, first, note, second])

    (row,) = adjustments(history[-1], history)

    assert row["paid_amount"]["amount"] == "1000.00"
    assert row["note_amount"]["amount"] == "100.00"
    # 2,100 less 500 received before, 100 by note and 1,000 now.
    assert row["pending_amount"]["amount"] == "500.00"


def test_a_later_receipt_does_not_count_against_an_earlier_print() -> None:
    first = _receipt("500.00", number="1", on=date(2026, 4, 3))
    later = _receipt("1600.00", number="2", on=date(2026, 4, 9))
    history = an.parse_vouchers([SALE, first, later])

    (row,) = adjustments(history[1], history)
    assert row["pending_amount"]["amount"] == "1600.00"


def test_a_bill_raised_outside_the_history_keeps_only_its_own_figures() -> None:
    """An opening bill, or one older than the window: no pending is invented."""
    (receipt,) = an.parse_vouchers([_receipt("2100.00")])
    (row,) = adjustments(receipt, [receipt])

    assert row["name"] == "SMA26/002"
    assert row["paid_amount"]["amount"] == "2100.00"
    assert row["bill_amount"] is None
    assert row["pending_amount"] is None


def test_no_history_at_all_is_not_a_bill_with_nothing_pending() -> None:
    (receipt,) = an.parse_vouchers([_receipt("2100.00")])
    (row,) = adjustments(receipt, None)
    assert row["pending_amount"] is None


def test_bare_bill_names_from_an_older_read_still_find_the_bill() -> None:
    """Vouchers stored before allocations carried a type and an amount."""
    sale = _voucher(
        "sales",
        "SMA26/002",
        date(2026, 4, 1),
        _money("2100.00", "debit"),
        ("Sales", _money("2100.00", "credit")),
        bill_references=["SMA26/002"],
    )
    receipt = _voucher(
        "receipt",
        "1",
        date(2026, 4, 5),
        _money("2100.00", "credit"),
        ("ICICI Bank", _money("2100.00", "debit")),
        bill_references=["SMA26/002"],
    )
    history = an.parse_vouchers([sale, receipt])

    (row,) = adjustments(history[1], history)
    assert row["bill_amount"]["amount"] == "2100.00"
    assert row["bill_date"] == "2026-04-01"
    assert row["pending_amount"]["amount"] == "0.00"


def test_a_statement_row_names_the_other_side_of_the_voucher() -> None:
    """Tally's Ledger Vouchers prints "Customization" against a customer's
    invoice and the bank against their receipt -- never the customer's own
    name on every row of the customer's own statement."""
    history = an.parse_vouchers([SALE, _receipt("2100.00")])
    statement = an.ledger_statement(
        history, ledger=PARTY, since=date(2026, 4, 1), until=date(2026, 4, 30)
    )
    assert [row["particulars"] for row in statement["entries"]] == ["Sales", "ICICI Bank"]


def test_a_bank_line_and_its_bills_reach_the_voucher_detail() -> None:
    receipt = an.parse_vouchers(
        [
            {
                **_receipt("2100.00"),
                "ledger_entries": [
                    *_receipt("2100.00")["ledger_entries"][:1],
                    {
                        "ledger_name": "ICICI Bank",
                        "amount": _money("2100.00", "debit"),
                        "bank": {
                            "transaction_type": "Cheque/DD",
                            "instrument_number": "2132001",
                            "instrument_date": "2026-04-01",
                            "bank_name": "State Bank of India (India)",
                        },
                    },
                ],
            }
        ]
    )[0]
    lines = {line["ledger"]: line for line in an.voucher_detail(receipt)["ledger_entries"]}

    assert lines["ICICI Bank"]["bank"]["instrument_number"] == "2132001"
    assert lines["ICICI Bank"]["bank"]["instrument_date"] == "2026-04-01"
    assert lines[PARTY]["bank"] is None
    assert lines[PARTY]["bills"][0]["bill_type"] == "Agst Ref"
