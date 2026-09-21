"""Real PostgreSQL fixtures with a private schema and advisory locks per test."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.database_isolation import isolated_test_database


@pytest_asyncio.fixture
async def isolated_database(test_database_url, monkeypatch):
    from src.scrapers import banner, lock, rmp

    async with isolated_test_database(test_database_url) as database:
        # Patch both the defining module and the aliases imported by scrapers.
        # All connections in this test still contend for the same real locks.
        monkeypatch.setattr(lock, "BANNER_SCRAPER_LOCK_ID", database.banner_lock_id)
        monkeypatch.setattr(banner, "BANNER_SCRAPER_LOCK_ID", database.banner_lock_id)
        monkeypatch.setattr(lock, "RMP_SCRAPER_LOCK_ID", database.rmp_lock_id)
        monkeypatch.setattr(rmp, "RMP_SCRAPER_LOCK_ID", database.rmp_lock_id)
        yield database


@pytest_asyncio.fixture
async def db_session_factory(isolated_database):
    """Every connection requested by one test uses that test's private schema."""
    return isolated_database.session_factory


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    async with db_session_factory() as session:
        # A realistic fixed code is safe inside a uniquely owned namespace.
        await session.execute(text("""
            INSERT INTO courses (course_code, title, credits)
            VALUES ('CS999', 'Test Course', 3)
        """))
        await session.commit()
        yield session
        # The session context rolls back failed transactions and closes before
        # isolated_database drops only this test's schema, even on test failure.


@pytest.fixture(autouse=True)
def no_upstream_throttling(monkeypatch):
    """Mocked upstream calls need no real production rate-limit/backoff waits.

    Replace only scraper module references, never the shared asyncio module;
    database concurrency tests must retain real event-loop scheduling.
    """
    from src.scrapers import banner, rmp

    for scraper in (banner, rmp):
        monkeypatch.setattr(scraper, "asyncio", SimpleNamespace(sleep=AsyncMock()))
