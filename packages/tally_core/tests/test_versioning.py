"""The shared version comparison.

Small surface, disproportionate consequences. Three codebases now decide "should
this update?" and every one of them has a way to be wrong that is silent for
months:

* Comparing as text stops offering updates the moment a release passes x.9.
* Treating an unknown version as ``0.0.0`` turns "I don't know" into a
  downgrade order, or into a client convinced it is permanently current.
* Refusing to go backwards is the only thing standing between a rolled-back
  manifest and a fleet that flaps between two builds indefinitely.

The Dart half of this lives in ``app_release.dart`` and its own tests mirror
these cases; if one side changes, both suites have to.
"""

from __future__ import annotations

import pytest

from tally_core.versioning import compare_versions, is_newer, parse_version


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        # The release at which string comparison silently stops working:
        # "0.10.0" sorts before "0.9.0" as text.
        ("0.10.0", "0.9.0", True),
        ("1.0.0", "0.99.99", True),
        # Two-part versions are the same build as their three-part spelling.
        ("1.0.1", "1.0", True),
        ("1.0", "1.0.0", False),
    ],
)
def test_is_newer(candidate: str, current: str, expected: bool) -> None:
    assert is_newer(candidate, current) is expected


def test_a_release_candidate_matches_its_release() -> None:
    """`0.2.0-rc1` and `0.2.0` are one build as far as updating goes.

    Ordering them would mean deciding whether `rc` precedes `beta`, which is a
    question nothing in this system needs answered.
    """
    assert is_newer("0.2.0-rc1", "0.2.0") is False
    assert is_newer("0.2.0", "0.2.0-rc1") is False
    assert compare_versions("0.2.0-rc1", "0.2.0") == 0


@pytest.mark.parametrize(
    ("candidate", "current"),
    [("", "0.1.0"), ("0.2.0", ""), ("", "")],
)
def test_an_unknown_version_never_triggers_an_update(candidate: str, current: str) -> None:
    """An empty version is "no opinion", never version zero.

    This is the one that would do real damage: a backend with no release
    manifest sends empty strings, and reading those as ``0.0.0`` would have it
    telling every connector in the fleet that it is ahead of the world.
    """
    assert is_newer(candidate, current) is False


def test_missing_segments_count_as_zero() -> None:
    assert compare_versions("1.0", "1.0.0") == 0
    assert compare_versions("1.0.0.0", "1.0") == 0
    assert compare_versions("1.0.1", "1.0") > 0


def test_compare_is_a_standard_comparator() -> None:
    assert compare_versions("0.1.0", "0.2.0") < 0
    assert compare_versions("0.2.0", "0.1.0") > 0
    assert compare_versions("0.2.0", "0.2.0") == 0


def test_versions_parse_to_numbers() -> None:
    assert parse_version("0.10.2") == (0, 10, 2)
    assert parse_version("1.2.3-rc4") == (1, 2, 3)
    # Junk becomes zero rather than raising: a malformed version in a manifest
    # should make the comparison uninteresting, not take the process down.
    assert parse_version("not-a-version") == (0,)
