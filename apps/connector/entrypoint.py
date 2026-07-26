"""Frozen-executable entry point.

PyInstaller executes its target as a top-level script named ``__main__``, which
means the relative imports inside ``tally_connector.main`` have no parent package
to resolve against. Pointing the spec at this shim instead keeps the package
importable the normal way, so the exe and ``python -m tally_connector.main``
follow the exact same code path.
"""

import sys

from tally_connector.main import run

if __name__ == "__main__":
    sys.exit(run())
