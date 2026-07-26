"""Async engine and session management."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import Settings
from .models import Base

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, object] = {"echo": settings.debug, "future": True}

    if settings.database_url.startswith("sqlite"):
        # SQLite has no meaningful pool and rejects the pool_* arguments below.
        # It is for tests and single-machine dev only; prod is Postgres.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
            # Costs one round trip per checkout, but turns "server closed the
            # connection unexpectedly" after a failover into a transparent retry.
            pool_pre_ping=True,
        )

    return create_async_engine(settings.database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,  # let handlers read attributes after commit
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transactional scope for background work that has no request to hang off."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly.

    For tests and first-run dev only. Schema changes in staging and prod go
    through Alembic so that data survives them.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
