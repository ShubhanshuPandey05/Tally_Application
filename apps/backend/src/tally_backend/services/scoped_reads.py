"""A :class:`ReadService` that only hands out what one person may see.

The API builds one of these for every company-scoped request by somebody with
an :class:`~.access_policy.AccessPolicy`, in place of the plain service. Every
report, the dashboard and the entry pickers already read through
``ReadService``, so filtering here is what makes a hidden ledger missing from
all of them -- including reports written later -- rather than from whichever
handlers remembered to filter.

Filtering happens on the way *out*. A live read is still stored whole: the
snapshot belongs to the company, and the next person to read it may be an admin.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..db.models import Company
from .access_policy import AccessScope
from .reads import DataResult, ReadService


class ScopedReadService(ReadService):
    def __init__(self, *args: Any, scope: AccessScope, company_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self._company_id = company_id

    def _filter(self, company: Company, dataset: str, payload: Any) -> Any:
        # The scope was resolved for one company. Applying it to another would
        # be using one set of books' names to filter a different set.
        if company.id != self._company_id:
            raise RuntimeError("access scope used against another company")
        return self.scope.filter_payload(dataset, payload)

    def _result(self, company: Company, dataset: str, result: DataResult) -> DataResult:
        return replace(result, payload=self._filter(company, dataset, result.payload))

    async def fetch(self, company: Company, *, dataset: str, **kwargs: Any) -> DataResult:
        result = await super().fetch(company, dataset=dataset, **kwargs)
        return self._result(company, dataset, result)

    async def fetch_vouchers(self, company: Company, **kwargs: Any) -> DataResult:
        result = await super().fetch_vouchers(company, **kwargs)
        return self._result(company, "vouchers.list", result)

    async def fetch_voucher(self, company: Company, **kwargs: Any) -> DataResult | None:
        result = await super().fetch_voucher(company, **kwargs)
        return None if result is None else self._result(company, "vouchers.list", result)

    async def stored_vouchers(self, company: Company, **kwargs: Any) -> Any | None:
        payload = await super().stored_vouchers(company, **kwargs)
        return None if payload is None else self._filter(company, "vouchers.list", payload)

    async def stored_history(self, company: Company, **kwargs: Any) -> DataResult | None:
        result = await super().stored_history(company, **kwargs)
        return None if result is None else self._result(company, "vouchers.list", result)

    async def snapshot_payload(self, company: Company, *, dataset: str, **kwargs: Any) -> Any:
        payload = await super().snapshot_payload(company, dataset=dataset, **kwargs)
        return None if payload is None else self._filter(company, dataset, payload)

    async def put_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        # Every write of a snapshot from here would store a filtered copy of the
        # books as though it were the whole of them.
        raise RuntimeError("a scoped read service never writes snapshots")
