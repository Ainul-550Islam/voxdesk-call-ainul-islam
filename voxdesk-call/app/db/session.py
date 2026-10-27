"""
Database session factory.

The engine is created lazily. Building it at import time means every module
that merely *mentions* the database drags in the asyncpg driver, which breaks
unit tests, CLI scripts and any tooling that does not talk to Postgres. A
module-level `__getattr__` keeps the familiar `from app.db.session import
engine` import working while deferring the actual connection setup.

An ``AsyncEngine`` pool holds live asyncpg connections, and those connections
are bound to the event loop that opened them. Caching one process-wide engine
therefore breaks any program that starts a second loop (``asyncio.run`` per
job, a test function per event loop, a worker restart): the pooled connection
raises ``RuntimeError: ... got Future ... attached to a different loop``.
Engines are cached per running loop instead; pools belonging to closed loops
are disposed on the next lookup, so a long-lived process cannot accumulate
them. Callers that ask for an engine outside a loop (import time, CLI setup)
share one ambient engine, exactly as before.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

from app.core.config import settings

_engine_lock = threading.Lock()
_loop_engines: dict[int, tuple[asyncio.AbstractEventLoop, AsyncEngine]] = {}
_ambient_engine: AsyncEngine | None = None


def _build_engine() -> AsyncEngine:
    if settings.database_url.startswith("sqlite"):
        return create_async_engine(
            settings.database_url,
            echo=False,
        )
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,     # drop dead connections instead of erroring mid-call
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        echo=False,
    )


def _dispose_stale(engine: AsyncEngine) -> None:
    """Best-effort synchronous pool shutdown for a loop that has ended."""
    try:
        engine.sync_engine.dispose()
    except Exception:  # noqa: BLE001 - cleanup must never break request handling
        pass


def get_engine() -> AsyncEngine:
    """Return the engine bound to the current event loop.

    A loop id is reused after garbage collection, so the loop object itself is
    stored alongside the engine and compared by identity. Closing a loop does
    not imply disposing its pool, hence the explicit prune below.
    """
    global _ambient_engine
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    with _engine_lock:
        if loop is None:
            if _ambient_engine is None:
                _ambient_engine = _build_engine()
            return _ambient_engine
        key = id(loop)
        entry = _loop_engines.get(key)
        if entry is not None and entry[0] is loop:
            return entry[1]
        for stale_key, (stale_loop, stale_engine) in list(_loop_engines.items()):
            if stale_loop is loop or stale_loop.is_closed():
                _dispose_stale(stale_engine)
                del _loop_engines[stale_key]
        engine = _build_engine()
        _loop_engines[key] = (loop, engine)
        return engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """A session factory for the current loop's engine.

    Deliberately not process-cached: a cached factory would keep pointing at
    the first loop's engine, which is the failure this module guards against.
    Building an ``async_sessionmaker`` is a plain object allocation.
    """
    return async_sessionmaker(
        get_engine(), class_=AsyncSession, expire_on_commit=False
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. One session per request, always closed."""
    async with get_sessionmaker()() as session:
        yield session


def __getattr__(name: str):
    """Backwards-compatible lazy attributes: `engine`, `SessionLocal`."""
    if name == "engine":
        return get_engine()
    if name == "SessionLocal":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
