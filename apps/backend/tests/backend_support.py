"""Test helpers shared by the backend suite's conftest and its test modules.

Separate from ``conftest.py`` for one specific reason: the connector suite has
a ``conftest.py`` of its own, and both directories go on ``sys.path`` when the
two are collected in the same run. ``from conftest import ...`` then resolves to
whichever was imported first, which is a collection error in one suite caused by
a file in the other.
"""

from __future__ import annotations

from contextlib import AsyncExitStack


class LifespanRunner:
    """Runs the app's lifespan without spinning up a server."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        await self._stack.enter_async_context(self._app.router.lifespan_context(self._app))
        return self._app

    async def __aexit__(self, *exc_info) -> None:
        await self._stack.aclose()
