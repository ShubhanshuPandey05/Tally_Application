"""TallyFlow Windows Connector.

The only component that speaks to TallyPrime. It holds an outbound WebSocket to
the backend, executes named read queries from ``tally_core``'s registry, and
returns typed results. It contains no product logic and no write path.
"""

__version__ = "0.2.4"
