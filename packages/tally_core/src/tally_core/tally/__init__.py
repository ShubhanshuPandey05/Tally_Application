"""TallyPrime protocol layer.

Importing this package registers every built-in query, so ``get_query`` works
immediately after ``import tally_core.tally``.
"""

from . import queries as _queries  # noqa: F401  (import registers queries)
from .client import TallyClient, TallyConfig
from .codec import parse_xml, sanitize_xml
from .envelope import Collection, StaticVariables, build_export_envelope
from .errors import (
    CompanyNotLoadedError,
    TallyBusyError,
    TallyCrashedError,
    TallyError,
    TallyParseError,
    TallyResponseError,
    TallyTimeoutError,
    TallyUnreachableError,
    UnknownQueryError,
)
from .query import QueryParams, TallyQuery, get_query, register, registry_manifest

__all__ = [
    "Collection",
    "CompanyNotLoadedError",
    "QueryParams",
    "StaticVariables",
    "TallyBusyError",
    "TallyClient",
    "TallyCrashedError",
    "TallyConfig",
    "TallyError",
    "TallyParseError",
    "TallyQuery",
    "TallyResponseError",
    "TallyTimeoutError",
    "TallyUnreachableError",
    "UnknownQueryError",
    "build_export_envelope",
    "get_query",
    "parse_xml",
    "register",
    "registry_manifest",
    "sanitize_xml",
]
