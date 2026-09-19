"""Built-in Tally mutations.

Importing this package registers every mutation in the write registry, exactly
as :mod:`tally_core.tally.queries` does for reads.
"""

from .masters import CREATE_LEDGER, CREATE_STOCK_ITEM
from .vouchers import CREATE_VOUCHER

__all__ = ["CREATE_LEDGER", "CREATE_STOCK_ITEM", "CREATE_VOUCHER"]
