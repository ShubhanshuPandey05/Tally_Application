from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_xml():
    """Load a recorded Tally response by filename stem."""

    def _load(name: str) -> str:
        return (FIXTURE_DIR / f"{name}.xml").read_text(encoding="utf-8")

    return _load
