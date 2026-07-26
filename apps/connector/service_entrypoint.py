"""Entry point for the windowless background build.

Built with ``console=False`` so the scheduled task does not park a black
console window on the shop's desktop all day. It takes no arguments: the only
thing this executable may ever do is ``run``, which keeps the always-on process
incapable of being pointed at ``pair`` or any future write command by a stray
argument.

It also names a log directory. That is not a duplicate of the ``log_dir`` the
installer writes into connector.json -- it is the floor beneath it. If that file
is hand-edited, or the connector is copied to a machine without one, this build
has no console to fall back to and would otherwise run completely mute.
"""

import sys

from tally_connector.install import default_log_dir
from tally_connector.main import run

if __name__ == "__main__":
    sys.exit(run(["run"], fallback_log_dir=default_log_dir()))
