"""TallyPrime protocol layer.

Importing this package registers every built-in query and mutation, so
``get_query`` and ``get_mutation`` both work immediately after
``import tally_core.tally``.
"""

from . import queries as _queries  # noqa: F401  (import registers queries)
from .client import TallyClient, TallyConfig
from .codec import parse_xml, sanitize_xml
from .envelope import (
    Collection,
    StaticVariables,
    build_export_envelope,
    build_import_envelope,
)
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
from .mutation import (
    MutationParams,
    TallyMutation,
    get_mutation,
    mutation_manifest,
    register_mutation,
)
from .mutations import (  # noqa: F401  (import registers mutations)
    CREATE_LEDGER,
    CREATE_STOCK_ITEM,
    CREATE_VOUCHER,
)
from .query import QueryParams, TallyQuery, get_query, register, registry_manifest

__all__ = [
    "CREATE_LEDGER",
    "CREATE_STOCK_ITEM",
    "CREATE_VOUCHER",
    "Collection",
    "MutationParams",
    "CompanyNotLoadedError",
    "QueryParams",
    "StaticVariables",
    "TallyBusyError",
    "TallyClient",
    "TallyCrashedError",
    "TallyConfig",
    "TallyError",
    "TallyParseError",
    "TallyMutation",
    "TallyQuery",
    "TallyResponseError",
    "TallyTimeoutError",
    "TallyUnreachableError",
    "UnknownQueryError",
    "build_export_envelope",
    "build_import_envelope",
    "get_mutation",
    "get_query",
    "mutation_manifest",
    "parse_xml",
    "register",
    "register_mutation",
    "registry_manifest",
    "sanitize_xml",
]
