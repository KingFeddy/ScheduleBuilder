"""
Shared fixtures for scraper tests.

db_session connects to the real dev database (DATABASE_URL from .env).
Tests run against real Postgres — no mocks for DB behavior.
Each test gets a fresh session. Cleanup runs after every test.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


# Professor names created by RMP cache tests — cleaned up after each test.
_TEST_PROFESSOR_NAMES = (
    "Dr. Smith",
    "Unknown Prof",
    "Ghost Prof",
    "Old Prof",
    "Prof X",
    "Some Prof",
)


@pytest_asyncio.fixture
async def db_session_factory():
    """Yields a session factory for tests that need multiple independent connections."""
    from src.config import settings

    engine = create_async_engine(settings.DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    from src.config import settings

    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # Seed a stub course so the section FK in test_negative_open_seats
        # doesn't fail. ON CONFLICT DO NOTHING is safe if the course already exists.
        await session.execute(
            text("""
                INSERT INTO courses (course_code, title, credits)
                VALUES ('CS999', 'Test Course', 3)
                ON CONFLICT (course_code) DO NOTHING
            """)
        )
        await session.commit()

        yield session

        # ── Cleanup ──────────────────────────────────────────────────────────
        # Remove test section and its meetings (FK order: meetings first)
        await session.execute(
            text("DELETE FROM meetings WHERE crn = '99999'")
        )
        await session.execute(
            text("DELETE FROM sections WHERE crn = '99999'")
        )
        await session.execute(
            text("DELETE FROM courses WHERE course_code = 'CS999'")
        )

        # Remove the uncatalogued-course stub created by
        # test_uncatalogued_course_gets_stub_row_before_section_insert
        await session.execute(
            text("DELETE FROM meetings WHERE crn = '88888'")
        )
        await session.execute(
            text("DELETE FROM sections WHERE crn = '88888'")
        )
        await session.execute(
            text("DELETE FROM courses WHERE course_code = 'CS998'")
        )

        # Remove rows created by the stale-section-cleanup tests. ZZZ997/77777
        # is expected to already be gone (that's what the test verifies) —
        # these deletes are idempotent safety nets, not the primary cleanup.
        # ZZZ is a reserved fake subject prefix, never a real NJIT subject —
        # see _FAKE_SUBJECT in test_banner_resilience.py for why that matters.
        for crn in ("77777", "89999", "66666"):
            await session.execute(
                text("DELETE FROM meetings WHERE crn = :crn"), {"crn": crn}
            )
            await session.execute(
                text("DELETE FROM sections WHERE crn = :crn"), {"crn": crn}
            )
        for code in ("ZZZ997", "ZZZ996", "ZZZ995"):
            await session.execute(
                text("DELETE FROM courses WHERE course_code = :code"), {"code": code}
            )

        # Remove scraper_runs created in the last 5 minutes (test runs are fast)
        await session.execute(
            text("""
                DELETE FROM scraper_runs
                WHERE scraper = 'banner'
                AND started_at > NOW() - INTERVAL '5 minutes'
            """)
        )

        # Remove RMP cache entries created by tests
        for name in _TEST_PROFESSOR_NAMES:
            await session.execute(
                text("DELETE FROM rmp_cache WHERE professor_name = :name"),
                {"name": name},
            )

        await session.commit()

    await engine.dispose()
