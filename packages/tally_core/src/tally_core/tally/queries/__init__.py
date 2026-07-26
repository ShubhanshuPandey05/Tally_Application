"""Importing this package is what populates the query registry.

Every module here must be imported for its ``@register`` decorators to run, so
new query modules must be added to ``__all__`` below as well as imported.
"""

from . import masters, transactions

__all__ = ["masters", "transactions"]
