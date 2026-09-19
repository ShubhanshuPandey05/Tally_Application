"""The mutation registry: the contract for everything that writes to Tally.

This mirrors :mod:`tally_core.tally.query` deliberately — same naming, same
registration decorator, same manifest — so there is nothing new to learn. What
is *not* shared is the registry itself.

**Reads and writes live in separate dictionaries on purpose.** ``get_query``
cannot return a mutation and ``get_mutation`` cannot return a query, whatever
name it is handed. The connector resolves an incoming read through the first and
an incoming write through the second, so the only way a read job reaches Tally's
writer is for somebody to change which function the read path calls — a visible
edit in a reviewed file, rather than a name collision in a shared table nobody
looked at.

Two rules that do not apply to reads apply to every mutation:

1. **A write is never retried on a timeout.** A read that times out is repeated
   at worst; an import that times out may have *already posted* — Tally can
   finish the work and lose the reply. Retrying it books the voucher twice, and
   a duplicated receipt in somebody's books is far worse than an error message.
   :attr:`TallyMutation.retry_on_timeout` is ``False`` and there is no way to
   set it to anything else.
2. **A write is never cached.** There is no answer to reuse, and an entry in the
   cache keyed like a read is how a second attempt silently returns the first
   attempt's receipt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar
from xml.etree import ElementTree as ET

from pydantic import BaseModel

from .errors import UnknownQueryError

ParamsT = TypeVar("ParamsT", bound=BaseModel)
ResultT = TypeVar("ResultT")


class MutationParams(BaseModel):
    """Base for mutation parameters.

    ``company`` is required for the same reason it is on a read, and for one
    more: Tally imports into whichever company is *active* when the envelope
    does not name one, so an unpinned write posts into whichever company the
    person at the till last clicked.
    """

    company: str


class TallyMutation(ABC, Generic[ParamsT, ResultT]):
    """A named, versioned Tally write."""

    #: Stable dotted identifier sent over the wire. Never rename — add a new one.
    name: ClassVar[str]
    #: Bumped when the request or response shape changes.
    version: ClassVar[int] = 1
    #: Pydantic model used to validate incoming params on the connector side.
    params_model: ClassVar[type[BaseModel]]

    #: Never overridden. See the module docstring: a retried import is a
    #: duplicated voucher, and Tally gives no way to tell the difference
    #: afterwards.
    retry_on_timeout: ClassVar[bool] = False

    @abstractmethod
    def build(self, params: ParamsT) -> str:
        """Return the ``Import`` envelope to POST to Tally."""

    @abstractmethod
    def parse(self, root: ET.Element, params: ParamsT) -> ResultT:
        """Convert Tally's ``<IMPORTRESULT>`` into a domain object."""

    def validate_params(self, raw: dict[str, Any]) -> ParamsT:
        return self.params_model.model_validate(raw)  # type: ignore[return-value]


_MUTATIONS: dict[str, TallyMutation[Any, Any]] = {}


def register_mutation(
    mutation_cls: type[TallyMutation[Any, Any]],
) -> type[TallyMutation[Any, Any]]:
    """Class decorator that adds a mutation to the write registry."""
    instance = mutation_cls()
    if instance.name in _MUTATIONS:
        raise RuntimeError(f"duplicate Tally mutation name: {instance.name}")
    _MUTATIONS[instance.name] = instance
    return mutation_cls


def get_mutation(name: str) -> TallyMutation[Any, Any]:
    try:
        return _MUTATIONS[name]
    except KeyError:
        raise UnknownQueryError(f"no registered Tally mutation named {name!r}") from None


def mutation_manifest() -> list[dict[str, Any]]:
    """Describe every registered mutation.

    Sent on handshake beside the read manifest. The backend uses it to refuse a
    write *before* sending it to a connector too old to understand one — an old
    connector ignores an unknown frame by design, which is the right behaviour
    and also means the phone would otherwise wait out the full deadline for a
    reply that was never coming.
    """
    return sorted(
        ({"name": m.name, "version": m.version} for m in _MUTATIONS.values()),
        key=lambda item: item["name"],
    )
