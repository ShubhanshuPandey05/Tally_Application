"""TallyFlow Windows Connector.

The only component that speaks to TallyPrime. It holds an outbound WebSocket to
the backend, executes named read queries from ``tally_core``'s registry, and
returns typed results. It contains no product logic, and its one write path --
creating a voucher -- goes through a separate, named mutation registry.
"""

__version__ = "0.5.0"
