"""What one staff member may see *inside* a company they were given.

``company_access`` answers "may this person open these books at all?". This
answers the next question an owner asks -- "the cashier should see the day book
but not salaries, and not the stock we keep for the other shop" -- along three
trees, each switched on as a whole and refinable underneath:

* **Reports** -- the report screens (``REPORT_KEYS``) and the Top Prime Reports
  (``top:<key>``), each section switchable on its own.
* **Ledger groups** -- Tally's own group tree, to any depth, down to single
  ledgers.
* **Stock groups** -- each item's stock group, down to single items.

**Everything is allowed until an admin says otherwise**, which is the opposite of
``company_access`` and on purpose: once somebody has been given a company, the
reasonable default is that they see it the way the admin does, and the policy
records only the exceptions. ``None`` on the row means "no exceptions".

**Enforced on the server, at the read.** :class:`ScopedReadService` filters every
dataset on its way out of ``ReadService``, so a hidden ledger is missing from the
ledger report, from receivables, from the day book and from the dashboard by
construction rather than because each handler remembered. The app hides switched
off report rows too, but that is to keep people out of dead ends; the 403 behind
them is the control.

A voucher is hidden when it touches *anything* hidden -- its party, any ledger
line or any stock line. Showing the rest of it would print a hidden salary in the
cash line of the same entry, and totals are worked out from what is left, so a
total never includes a figure the person could not have opened.

Names, not GUIDs, because that is what every voucher line carries and what the
admin chose from. A ledger renamed in Tally after being hidden is a different
name and shows again; the admin sees it in the list under its new name.

Fail closed. When groups are restricted and the masters needed to place a ledger
in its group have not been read, that ledger is hidden: a restriction that lapses
whenever a snapshot is missing is not a restriction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..core.errors import PermissionDenied

#: Every switchable report on the Reports screen. The app's rows carry the same
#: keys; a key the server does not know is refused rather than stored, so a typo
#: never becomes a restriction that silently restricts nothing.
STANDARD_REPORTS: tuple[str, ...] = (
    "receivables",
    "payables",
    "sundry_debtors",
    "sundry_creditors",
    "ledgers",
    "daybook",
    "sales_register",
    "purchase_register",
    "stock_summary",
    "godown_summary",
    "running_low",
    "negative_stock",
    "slow_movers",
)

#: Prime reports are keyed ``top:<key>``, the backend's own Top report keys.
PRIME_PREFIX = "top:"

#: Bounds on what one policy may list. Generous -- a big chart of accounts has a
#: few thousand ledgers -- but finite, because this is sealed and stored per row.
MAX_NAMES = 20_000
MAX_NAME_LENGTH = 300

#: Groups Tally reports as the root of its tree.
_ROOT_PARENTS = frozenset({"", "primary"})


def norm(name: str | None) -> str:
    """Tally names compare case-insensitively and ignore stray spaces."""
    return (name or "").strip().casefold()


def prime_report_keys() -> tuple[str, ...]:
    # Imported here: top_reports is large and nothing else in this module
    # needs it at import time.
    from .top_reports import REPORT_KEYS

    return tuple(f"{PRIME_PREFIX}{key}" for key in REPORT_KEYS)


def known_report_keys() -> frozenset[str]:
    return frozenset(STANDARD_REPORTS) | frozenset(prime_report_keys())


class AccessPolicy(BaseModel):
    """The exceptions an admin has set for one person in one company.

    The booleans are the top-level switches in the admin's screen; the lists
    are the rows switched off below them. A row under a switched-off parent is
    hidden whatever its own entry says, so turning a parent back on restores the
    children exactly as they were left.
    """

    #: "Reports Master": off hides every report.
    reports: bool = True
    #: The Reports screen's section.
    standard_reports: bool = True
    #: The Top Prime Reports section.
    prime_reports: bool = True
    denied_reports: list[str] = Field(default_factory=list)

    #: "Ledger Groups": off hides every ledger, and so every voucher.
    ledgers: bool = True
    denied_ledger_groups: list[str] = Field(default_factory=list)
    denied_ledgers: list[str] = Field(default_factory=list)

    #: "Stock Group": off hides every stock item.
    stock: bool = True
    denied_stock_groups: list[str] = Field(default_factory=list)
    denied_stock_items: list[str] = Field(default_factory=list)

    @field_validator("denied_reports")
    @classmethod
    def _known_reports(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - known_report_keys())
        if unknown:
            raise ValueError(f"unknown report keys: {unknown}")
        return sorted(set(value))

    @field_validator(
        "denied_ledger_groups",
        "denied_ledgers",
        "denied_stock_groups",
        "denied_stock_items",
    )
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_NAMES:
            raise ValueError(f"at most {MAX_NAMES} names")
        cleaned: dict[str, str] = {}
        for name in value:
            stripped = name.strip()
            if not stripped:
                continue
            if len(stripped) > MAX_NAME_LENGTH:
                raise ValueError(f"names are at most {MAX_NAME_LENGTH} characters")
            cleaned.setdefault(norm(stripped), stripped)
        return sorted(cleaned.values(), key=norm)

    @property
    def is_unrestricted(self) -> bool:
        return self == AccessPolicy()

    @property
    def restricts_ledgers(self) -> bool:
        return not self.ledgers or bool(self.denied_ledger_groups or self.denied_ledgers)

    @property
    def restricts_stock(self) -> bool:
        return not self.stock or bool(self.denied_stock_groups or self.denied_stock_items)

    def allows_report(self, key: str) -> bool:
        if not self.reports:
            return False
        section = self.prime_reports if key.startswith(PRIME_PREFIX) else self.standard_reports
        return section and key not in self.denied_reports

    @classmethod
    def from_stored(cls, stored: Any) -> AccessPolicy:
        """Read a stored policy, keeping only what still validates.

        A report key that a later release removed must not make the row
        unreadable -- that would lock the person out of the company -- so it is
        dropped and the rest of the policy kept.
        """
        if not stored:
            return cls()
        data = dict(stored)
        known = known_report_keys()
        data["denied_reports"] = [k for k in data.get("denied_reports", []) if k in known]
        return cls.model_validate(data)


# --------------------------------------------------------------------------
# Resolving names against the company's masters
# --------------------------------------------------------------------------


@dataclass
class Masters:
    """Just enough of the chart of accounts and stock list to place a name.

    ``None`` for a map means it could not be read -- which is different from
    an empty company and decides whether an unplaceable name is hidden.
    """

    #: group -> parent group, both normalised.
    group_parents: dict[str, str] | None = None
    #: ledger -> its group, both normalised.
    ledger_groups: dict[str, str] | None = None
    #: stock item -> its stock group, both normalised.
    item_groups: dict[str, str] | None = None


@dataclass
class AccessScope:
    """A policy, resolved against one company's masters, ready to filter rows."""

    policy: AccessPolicy
    masters: Masters = field(default_factory=Masters)
    _ledger_cache: dict[str, bool] = field(default_factory=dict)
    _item_cache: dict[str, bool] = field(default_factory=dict)
    _group_cache: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        p = self.policy
        self._denied_groups = {norm(n) for n in p.denied_ledger_groups}
        self._denied_ledgers = {norm(n) for n in p.denied_ledgers}
        self._denied_stock_groups = {norm(n) for n in p.denied_stock_groups}
        self._denied_items = {norm(n) for n in p.denied_stock_items}

    # -- reports ----------------------------------------------------------

    def require_report(self, key: str) -> None:
        if not self.policy.allows_report(key):
            raise PermissionDenied(
                f"report {key} is switched off for this user",
                user_message="Your admin has not given you access to this report.",
            )

    # -- ledgers ----------------------------------------------------------

    def group_visible(self, name: str | None) -> bool:
        """Whether a ledger group, and so everything under it, may be seen."""
        if not self.policy.ledgers:
            return False
        key = norm(name)
        if key in self._group_cache:
            return self._group_cache[key]
        visible = True
        parents = self.masters.group_parents
        seen: set[str] = set()
        current = key
        # Walk to the root. `seen` guards against a cycle in a malformed read,
        # which would otherwise spin this request forever.
        while current and current not in _ROOT_PARENTS and current not in seen:
            if current in self._denied_groups:
                visible = False
                break
            if self._denied_groups and (parents is None or current not in parents):
                # A group we cannot place under its parents may sit inside a
                # hidden one. Fail closed (see the module docstring).
                visible = False
                break
            seen.add(current)
            current = (parents or {}).get(current, "")
        self._group_cache[key] = visible
        return visible

    def ledger_visible(self, name: str | None) -> bool:
        if not self.policy.restricts_ledgers:
            return True
        if not self.policy.ledgers:
            return False
        key = norm(name)
        if not key:
            return True
        cached = self._ledger_cache.get(key)
        if cached is not None:
            return cached
        if key in self._denied_ledgers:
            visible = False
        elif not self._denied_groups:
            visible = True
        else:
            groups = self.masters.ledger_groups
            group = groups.get(key) if groups is not None else None
            # Unplaceable with a group restriction in force: hidden. See the
            # module docstring -- failing open here would let every ledger
            # created since the last master read through.
            visible = group is not None and self.group_visible(group)
        self._ledger_cache[key] = visible
        return visible

    # -- stock ------------------------------------------------------------

    def stock_group_visible(self, name: str | None) -> bool:
        return self.policy.stock and norm(name) not in self._denied_stock_groups

    def item_visible(self, name: str | None) -> bool:
        if not self.policy.restricts_stock:
            return True
        if not self.policy.stock:
            return False
        key = norm(name)
        if not key:
            return True
        cached = self._item_cache.get(key)
        if cached is not None:
            return cached
        if key in self._denied_items:
            visible = False
        elif not self._denied_stock_groups:
            visible = True
        else:
            groups = self.masters.item_groups
            group = groups.get(key) if groups is not None else None
            visible = group is not None and group not in self._denied_stock_groups
        self._item_cache[key] = visible
        return visible

    # -- vouchers ---------------------------------------------------------

    def voucher_visible(self, voucher: dict[str, Any]) -> bool:
        """Hidden when it names anything hidden: party, ledger line or item."""
        if not isinstance(voucher, dict):
            return True
        if self.policy.restricts_ledgers:
            if not self.ledger_visible(voucher.get("party_name")):
                return False
            for line in voucher.get("ledger_entries") or ():
                if isinstance(line, dict) and not self.ledger_visible(line.get("ledger_name")):
                    return False
            for line in voucher.get("inventory_entries") or ():
                # An item line posts to a sales or purchase ledger of its own.
                if isinstance(line, dict) and not self.ledger_visible(line.get("ledger_name")):
                    return False
        if self.policy.restricts_stock:
            for line in voucher.get("inventory_entries") or ():
                if isinstance(line, dict) and not self.item_visible(line.get("item_name")):
                    return False
        return True

    def entry_refusal(self, draft: dict[str, Any]) -> str | None:
        """Why this person may not post ``draft``, or ``None`` when they may.

        Like :meth:`voucher_visible` but failing *open* for a name the masters
        have never seen: a party being entered for the first time is not in
        any group yet, and Tally itself refuses a ledger that does not exist
        -- which is what starts the "add this party?" flow. Refusing it here
        would take that flow away from everyone with a group restriction.
        """
        def known_hidden_ledger(name: str | None) -> bool:
            key = norm(name)
            if not key or not self.policy.restricts_ledgers:
                return False
            known = self.masters.ledger_groups is not None and key in self.masters.ledger_groups
            return (not self.policy.ledgers or key in self._denied_ledgers) or (
                known and not self.ledger_visible(name)
            )

        def known_hidden_item(name: str | None) -> bool:
            key = norm(name)
            if not key or not self.policy.restricts_stock:
                return False
            known = self.masters.item_groups is not None and key in self.masters.item_groups
            return (not self.policy.stock or key in self._denied_items) or (
                known and not self.item_visible(name)
            )

        names = [draft.get("party_name")]
        names += [line.get("ledger_name") for line in draft.get("ledger_entries") or ()]
        names += [line.get("ledger_name") for line in draft.get("inventory_entries") or ()]
        for name in names:
            if known_hidden_ledger(name):
                return f"ledger {name!r}"
        for line in draft.get("inventory_entries") or ():
            if known_hidden_item(line.get("item_name")):
                return f"item {line.get('item_name')!r}"
        return None

    # -- datasets ---------------------------------------------------------

    def filter_payload(self, dataset: str, payload: Any) -> Any:
        """The rows of ``dataset`` this person may see.

        Returns a new list and never mutates ``payload``: the same object may be
        the one a snapshot was just written from.
        """
        if not isinstance(payload, list):
            return payload
        keep = self._row_filter(dataset)
        if keep is None:
            return payload
        return [row for row in payload if not isinstance(row, dict) or keep(row)]

    def _row_filter(self, dataset: str) -> Callable[[dict[str, Any]], bool] | None:
        ledgers = self.policy.restricts_ledgers
        stock = self.policy.restricts_stock
        if dataset == "ledgers.list" and ledgers:
            return lambda row: self.ledger_visible(row.get("name"))
        if dataset == "groups.list" and ledgers:
            return lambda row: self.group_visible(row.get("name"))
        if dataset == "outstanding.bills" and ledgers:
            return lambda row: self.ledger_visible(row.get("party_name"))
        if dataset == "stock_items.list" and stock:
            return lambda row: self.item_visible(row.get("name"))
        if dataset == "godown_stock.list" and stock:
            return lambda row: self.item_visible(row.get("item_name"))
        if dataset == "vouchers.list" and (ledgers or stock):
            return self.voucher_visible
        return None


def masters_from_payloads(
    *, groups: Any | None, ledgers: Any | None, stock: Any | None
) -> Masters:
    """Build the lookup maps from raw dataset payloads (any may be missing)."""

    def rows(payload: Any) -> list[dict[str, Any]] | None:
        if payload is None:
            return None
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    group_rows, ledger_rows, stock_rows = rows(groups), rows(ledgers), rows(stock)
    return Masters(
        group_parents=None
        if group_rows is None
        else {norm(r.get("name")): norm(r.get("parent")) for r in group_rows},
        ledger_groups=None
        if ledger_rows is None
        else {norm(r.get("name")): norm(r.get("parent_group")) for r in ledger_rows},
        item_groups=None
        if stock_rows is None
        else {norm(r.get("name")): norm(r.get("parent_group")) for r in stock_rows},
    )


# --------------------------------------------------------------------------
# Loading and applying
# --------------------------------------------------------------------------

#: What the sealed policy column is bound to (see core.sealing).
SEAL_PURPOSE = "access.policy"


async def stored_policy(session: Any, access: Any) -> AccessPolicy:
    """Open one ``company_access`` row's policy."""
    from .data_keys import company_cipher

    if access is None or access.restrictions is None:
        return AccessPolicy()
    cipher = await company_cipher(session, access.company_id)
    return AccessPolicy.from_stored(cipher.open(access.restrictions, purpose=SEAL_PURPOSE))


async def seal_policy(session: Any, company_id: str, policy: AccessPolicy) -> str | None:
    """The value to store: ``None`` for "no exceptions", sealed otherwise."""
    from .data_keys import company_cipher

    if policy.is_unrestricted:
        return None
    cipher = await company_cipher(session, company_id, create=True)
    return cipher.seal(policy.model_dump(mode="json"), purpose=SEAL_PURPOSE)


async def load_scope(session: Any, reads: Any, company: Any, access: Any) -> AccessScope | None:
    """The scope for one person in one company, or ``None`` when unrestricted.

    Reads the masters straight from stored snapshots, never from Tally: this
    runs before every report a restricted person opens, and it must cost the
    shop's PC nothing. Only the masters a restriction actually needs are opened.
    """
    policy = await stored_policy(session, access)
    if policy.is_unrestricted:
        return None
    needs_ledgers = policy.ledgers and bool(policy.denied_ledger_groups)
    needs_stock = policy.stock and bool(policy.denied_stock_groups)
    groups = ledgers = stock = None
    if needs_ledgers:
        groups = await reads.snapshot_payload(company, dataset="groups.list")
        ledgers = await reads.snapshot_payload(company, dataset="ledgers.list")
    if needs_stock:
        stock = await reads.snapshot_payload(company, dataset="stock_items.list")
    return AccessScope(
        policy=policy,
        masters=masters_from_payloads(groups=groups, ledgers=ledgers, stock=stock),
    )
