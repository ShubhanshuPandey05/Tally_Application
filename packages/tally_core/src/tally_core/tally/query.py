"""The query registry: the contract between the backend and the connector.

The backend never sends XML over the wire. It sends ``{"query": "ledgers.list",
"params": {...}}``; the connector looks the name up here, builds the envelope,
talks to Tally, and returns typed domain objects. That indirection is what keeps
the connector free of business logic (CLAUDE.md: "it should simply translate
requests") and what lets a query's TDL be fixed by shipping a connector update
without touching the app.

Adding write support later means adding a ``TallyMutation`` subclass alongside
this one with the same registry mechanics -- no redesign.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar
from xml.etree import ElementTree as ET

from pydantic import BaseModel

from .errors import UnknownQueryError

ParamsT = TypeVar("ParamsT", bound=BaseModel)
ResultT = TypeVar("ResultT")


class QueryParams(BaseModel):
    """Base for query parameters.

    ``company`` is on the base because every read is scoped to exactly one
    company; a query that omits it would silently read whichever company the
    operator last clicked in Tally.
    """

    company: str


class TallyQuery(ABC, Generic[ParamsT, ResultT]):
    """A named, versioned, read-only Tally request."""

    #: Stable dotted identifier sent over the wire. Never rename -- add a new one.
    name: ClassVar[str]
    #: Bumped when the response shape changes, so the backend can detect skew.
    version: ClassVar[int] = 1
    #: Pydantic model used to validate incoming params on the connector side.
    params_model: ClassVar[type[BaseModel]]
    #: Rough cost hint used by the connector queue for scheduling and timeouts.
    heavy: ClassVar[bool] = False

    @abstractmethod
    def build(self, params: ParamsT) -> str:
        """Return the XML envelope to POST to Tally."""

    @abstractmethod
    def parse(self, root: ET.Element, params: ParamsT) -> ResultT:
        """Convert a parsed Tally response into domain objects."""

    def validate_params(self, raw: dict[str, Any]) -> ParamsT:
        return self.params_model.model_validate(raw)  # type: ignore[return-value]


_REGISTRY: dict[str, TallyQuery[Any, Any]] = {}


def register(query_cls: type[TallyQuery[Any, Any]]) -> type[TallyQuery[Any, Any]]:
    """Class decorator that adds a query to the global registry."""
    instance = query_cls()
    if instance.name in _REGISTRY:
        raise RuntimeError(f"duplicate Tally query name: {instance.name}")
    _REGISTRY[instance.name] = instance
    return query_cls


def get_query(name: str) -> TallyQuery[Any, Any]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownQueryError(f"no registered Tally query named {name!r}") from None


def registry_manifest() -> list[dict[str, Any]]:
    """Describe every registered query.

    The connector sends this on handshake so the backend knows exactly which
    capabilities that connector build supports, and can degrade gracefully when
    a customer is running an older connector.
    """
    return sorted(
        (
            {"name": q.name, "version": q.version, "heavy": q.heavy}
            for q in _REGISTRY.values()
        ),
        key=lambda item: item["name"],
    )
