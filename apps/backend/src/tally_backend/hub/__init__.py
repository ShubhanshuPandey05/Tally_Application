"""Connector fleet management: live sockets, routing, and job dispatch."""

from .bus import BusUnavailable, JobBus, LocalBus, RedisBus, build_bus
from .hub import ConnectorHub, coalesce_key
from .link import ConnectorLink, LinkClosed, SocketLike

__all__ = [
    "BusUnavailable",
    "ConnectorHub",
    "ConnectorLink",
    "JobBus",
    "LinkClosed",
    "LocalBus",
    "RedisBus",
    "SocketLike",
    "build_bus",
    "coalesce_key",
]
