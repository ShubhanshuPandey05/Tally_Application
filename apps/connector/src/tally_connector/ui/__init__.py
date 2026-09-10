"""The connector's local window.

A native window on the shop's PC that answers the questions somebody standing at
it actually has -- is this working, which of my companies does it feed, who can
see them -- and that pairs the machine in the first place by showing a code for
the phone to scan.

The window itself is a separate process (``apps/mobile``, built for Windows). It
is a client of what lives here: :class:`UiBridge` holds the state and the levers,
:class:`LocalUiServer` hands them out over loopback. The connector runs whether
or not the window has ever been opened.

Deliberately not a control panel. It cannot disconnect this computer, unlink a
company or remove a person: those decisions belong to whoever holds the account
on their phone, not to whoever is standing at the till. What it can do is
restart the connector, change the Tally port, refresh, and show a pairing code.
"""

from .server import LocalUiServer
from .state import UiBridge, UiState

__all__ = ["LocalUiServer", "UiBridge", "UiState"]
