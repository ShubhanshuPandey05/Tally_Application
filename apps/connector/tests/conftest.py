from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from tally_core.tally import TallyClient, TallyConfig

from tally_connector.cache import ResponseCache
from tally_connector.executor import JobExecutor
from tally_connector.pipeline import TallyPipeline

COMPANIES_XML = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    "<COMPANY NAME='Acme'><NAME>Acme</NAME><STARTINGFROM>20240401</STARTINGFROM></COMPANY>"
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)

#: No backoff: retry behaviour is covered in tally_core's own suite, and real
#: sleeps here would make this file slow for no extra coverage.
FAST_TALLY = TallyConfig(retry_backoff_seconds=0.0, max_attempts=1)


@pytest.fixture
def make_pipeline() -> Callable[..., TallyPipeline]:
    """Build a TallyPipeline in front of a scripted fake Tally."""

    def _make(handler, **kwargs) -> TallyPipeline:
        tally = TallyClient(
            FAST_TALLY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        # No cooldown by default: the pause is covered by its own test, and
        # paying it in every other one would make the suite slow for nothing.
        kwargs.setdefault("cooldown_seconds", 0.0)
        return TallyPipeline(tally, **kwargs)

    return _make


@pytest.fixture
def make_executor(make_pipeline) -> Callable[..., JobExecutor]:
    """Build a JobExecutor wired to a scripted fake Tally."""

    def _make(handler, *, cache: ResponseCache | None = None) -> JobExecutor:
        return JobExecutor(make_pipeline(handler), cache or ResponseCache())

    return _make


@pytest.fixture
def companies_xml() -> str:
    return COMPANIES_XML


@pytest.fixture
def ok_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=COMPANIES_XML)

    return handler
