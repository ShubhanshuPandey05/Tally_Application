"""The fields a receipt or payment print needs beyond the ledger lines.

The cheque behind the bank line and the bill each party line settled. Shaped
on a receipt as a live TallyPrime exports one -- the empty wrappers on the lines
that carry nothing are part of that shape, and have to stay nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tally_core.domain.money import Side
from tally_core.tally import get_query, parse_xml

RECEIPT = """
<ENVELOPE><BODY><DATA><COLLECTION>
 <VOUCHER VCHTYPE="Receipt">
  <DATE>20260401</DATE>
  <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
  <VOUCHERNUMBER>1</VOUCHERNUMBER>
  <PARTYLEDGERNAME>BEST SAYAR MEDICAL CENTER PALI</PARTYLEDGERNAME>
  <LEDGERENTRIES.LIST>
   <LEDGERNAME>BEST SAYAR MEDICAL CENTER PALI</LEDGERNAME>
   <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
   <AMOUNT>2100.00</AMOUNT>
   <BANKALLOCATIONS.LIST>      </BANKALLOCATIONS.LIST>
   <BILLALLOCATIONS.LIST>
    <BILLDATE TYPE="Date">20260401</BILLDATE>
    <NAME>SMA26/002</NAME>
    <BILLTYPE>Agst Ref</BILLTYPE>
    <BILLCREDITPERIOD>15 Days</BILLCREDITPERIOD>
    <AMOUNT>2100.00</AMOUNT>
   </BILLALLOCATIONS.LIST>
  </LEDGERENTRIES.LIST>
  <LEDGERENTRIES.LIST>
   <LEDGERNAME>ICICI Bank</LEDGERNAME>
   <AMOUNT>-2100.00</AMOUNT>
   <BANKALLOCATIONS.LIST>
    <DATE>20260401</DATE>
    <INSTRUMENTDATE>20260401</INSTRUMENTDATE>
    <TRANSACTIONTYPE>Cheque/DD</TRANSACTIONTYPE>
    <INSTRUMENTNUMBER>2132001</INSTRUMENTNUMBER>
    <BANKNAME>State Bank of India (India)</BANKNAME>
    <PAYMENTMODE>Transacted</PAYMENTMODE>
    <AMOUNT>-2100.00</AMOUNT>
   </BANKALLOCATIONS.LIST>
   <BILLALLOCATIONS.LIST>      </BILLALLOCATIONS.LIST>
  </LEDGERENTRIES.LIST>
 </VOUCHER>
</COLLECTION></DATA></BODY></ENVELOPE>
"""


def _receipt():
    query = get_query("vouchers.list")
    params = query.validate_params(
        {
            "company": "Shree Medical Agency",
            "from_date": date(2026, 4, 1),
            "to_date": date(2026, 4, 1),
        }
    )
    (voucher,) = query.parse(parse_xml(RECEIPT), params)
    return {e.ledger_name: e for e in voucher.ledger_entries}


def test_the_bank_line_carries_its_cheque():
    bank = _receipt()["ICICI Bank"].bank
    assert bank is not None
    assert bank.transaction_type == "Cheque/DD"
    assert bank.instrument_number == "2132001"
    assert bank.instrument_date == date(2026, 4, 1)
    # The customer's bank, which is what a receipt prints -- not our own.
    assert bank.bank_name == "State Bank of India (India)"


def test_an_empty_bank_wrapper_is_no_bank_at_all():
    """Tally sends ``<BANKALLOCATIONS.LIST>`` on every line of an export."""
    assert _receipt()["BEST SAYAR MEDICAL CENTER PALI"].bank is None


def test_the_party_line_carries_the_bill_it_settled():
    party = _receipt()["BEST SAYAR MEDICAL CENTER PALI"]
    (bill,) = party.bills
    assert bill.name == "SMA26/002"
    assert bill.bill_type == "Agst Ref"
    assert bill.bill_date == date(2026, 4, 1)
    assert bill.credit_days == 15
    assert bill.amount is not None
    assert bill.amount.amount == Decimal("2100.00")
    assert bill.amount.side is Side.CREDIT
    # The names still ride where every older reader looks for them.
    assert party.bill_references == ["SMA26/002"]


def test_a_line_with_no_bills_has_none():
    assert _receipt()["ICICI Bank"].bills == []


def test_the_envelope_asks_for_them():
    query = get_query("vouchers.list")
    xml = query.build(
        query.validate_params(
            {"company": "Acme", "from_date": date(2026, 4, 1), "to_date": date(2026, 4, 30)}
        )
    )
    for leaf in (
        "LedgerEntries.BillAllocations.BillType",
        "LedgerEntries.BillAllocations.Amount",
        "LedgerEntries.BankAllocations.InstrumentNumber",
        "LedgerEntries.BankAllocations.TransactionType",
    ):
        assert f"<FETCH>{leaf}</FETCH>" in xml
