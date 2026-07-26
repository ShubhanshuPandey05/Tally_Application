"""tally_core -- the TallyPrime protocol layer shared by the connector and the backend.

Layering::

    domain/   pure product models, know nothing about Tally
    tally/    envelopes, XML codec, query registry, mappers, HTTP transport

The connector imports both. The backend imports ``domain`` (and the query names)
but never speaks XML itself.
"""

__version__ = "0.1.0"
