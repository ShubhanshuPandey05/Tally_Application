"""One definition of "is this build out of date?", shared by backend and connector.

This lives in ``tally_core`` rather than in either app because the comparison is
now made in three places -- the connector deciding whether to install, the
backend deciding whether to order an install, and the app deciding whether to
block itself -- and two of them disagreeing is a fleet that either updates in a
loop or never updates at all. The Dart half in
``apps/mobile/lib/src/features/updates/domain/app_release.dart`` implements the
same rules; ``tests/test_versioning.py`` pins the cases both must agree on.

The rules, and why each one exists:

``Compare numerically, never as text.``
    ``"0.10.0" < "0.9.0"`` is true as a string and false as a version. The bug
    it causes is invisible for a year and then permanent: the first release past
    x.9 silently stops being offered to anybody.

``Missing components are zero.``
    ``0.2`` and ``0.2.0`` are the same build, so a manifest written by hand with
    a two-part version does not read as a downgrade.

``Non-numeric suffixes are dropped.``
    ``0.2.0-rc1`` compares equal to ``0.2.0``. A release candidate and its
    release are the same build as far as "do I need to upgrade?" goes, and the
    alternative -- ordering them -- means deciding whether ``rc1`` precedes
    ``beta``, which is a question nothing here needs answered.

``An empty version is no opinion.``
    Not version zero. A backend with no release manifest configured sends empty
    strings, and reading those as ``0.0.0`` would turn "I don't know" into "you
    are ahead of the world, never update" -- or worse, into a downgrade order.
"""

from __future__ import annotations

__all__ = ["compare_versions", "is_newer", "parse_version"]


def parse_version(value: str) -> tuple[int, ...]:
    """``"0.2.10"`` -> ``(0, 2, 10)``, for comparison rather than display."""
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def compare_versions(left: str, right: str) -> int:
    """Standard comparator contract: negative, zero, or positive.

    Both sides are zero-padded to the same length so ``0.2`` and ``0.2.0``
    compare equal instead of the shorter one always losing.
    """
    a = parse_version(left)
    b = parse_version(right)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a version worth installing over ``current``.

    Equal versions are not newer, and neither is an older one. Refusing to go
    backwards is what stops a rolled-back manifest from dragging the fleet down
    with it -- the installer would re-offer the newer build on the next check,
    and every connector would flap between two versions indefinitely.

    Either side being empty means somebody does not know what they are running,
    and the safe reading of "I don't know" is always "do nothing".
    """
    if not candidate or not current:
        return False
    return compare_versions(candidate, current) > 0
