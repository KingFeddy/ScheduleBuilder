"""Regression checks for ownership and cleanup of real PostgreSQL test data."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_database_fixtures_share_a_private_schema_and_real_commits(
    db_session, db_session_factory,
):
    schema = await db_session.scalar(text("SELECT current_schema()"))
    assert schema.startswith("test_"), "Test records must not live in the public schema"
    assert await db_session.scalar(text("SELECT current_schemas(false)")) == [schema]

    await db_session.execute(
        text("UPDATE courses SET title = :title WHERE course_code = 'CS999'"),
        {"title": "Committed in this test"},
    )
    await db_session.commit()

    async with db_session_factory() as other_session:
        assert await other_session.scalar(text("SELECT current_schema()")) == schema
        assert await other_session.scalar(
            text("SELECT title FROM courses WHERE course_code = 'CS999'")
        ) == "Committed in this test"


@pytest.mark.asyncio
async def test_scraper_lock_ids_are_scoped_to_this_test(db_session_factory):
    from src.scrapers import banner, lock, rmp

    assert banner.BANNER_SCRAPER_LOCK_ID == lock.BANNER_SCRAPER_LOCK_ID
    assert rmp.RMP_SCRAPER_LOCK_ID == lock.RMP_SCRAPER_LOCK_ID
    assert -(2**63) <= lock.BANNER_SCRAPER_LOCK_ID < 0
    assert -(2**63) <= lock.RMP_SCRAPER_LOCK_ID < 0
    assert lock.BANNER_SCRAPER_LOCK_ID != lock.RMP_SCRAPER_LOCK_ID


async def _schema_exists(database_url, schema_name):
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :name)"),
                {"name": schema_name},
            )
    finally:
        await engine.dispose()


async def _seed_records(session_factory, label):
    # Deliberately reuse the identifiers from the scraper regressions in every
    # namespace. Ownership must not depend on which courses or professors exist.
    statements = (
        "INSERT INTO courses (course_code, title, credits) VALUES ('CS999', :label, 3)",
        """INSERT INTO sections (crn, term, course_code, professor_name, location)
           VALUES ('99999', '202690', 'CS999', 'Dr. Smith', :label)""",
        """INSERT INTO meetings (crn, term, days, start_time, end_time, location)
           VALUES ('99999', '202690', 'M', '09:00', '10:00', :label)""",
        """INSERT INTO professors (professor_name, department)
           VALUES ('Dr. Smith', :label)""",
        """INSERT INTO scraper_runs (scraper, status, error_message)
           VALUES ('banner', 'completed', :label)""",
        """INSERT INTO rmp_cache (professor_name, rmp_data, expires_at)
           VALUES ('Dr. Smith', jsonb_build_object('label', CAST(:label AS text)),
                   NOW() + INTERVAL '1 day')""",
    )
    async with session_factory() as session:
        for statement in statements:
            await session.execute(text(statement), {"label": label})
        await session.commit()


async def _records(session_factory):
    async with session_factory() as session:
        result = await session.execute(text("""
            SELECT c.title, s.location AS section_location,
                   m.location AS meeting_location, p.department,
                   r.error_message, cache.rmp_data
            FROM courses c
            JOIN sections s USING (course_code)
            JOIN meetings m USING (crn, term)
            JOIN professors p USING (professor_name)
            JOIN rmp_cache cache USING (professor_name)
            CROSS JOIN scraper_runs r
        """))
        return dict(result.mappings().one())


@pytest.mark.asyncio
async def test_cleanup_preserves_other_tests_with_identical_record_keys(test_database_url):
    from src.scrapers.lock import advisory_lock
    from tests.database_isolation import isolated_test_database

    async with isolated_test_database(test_database_url) as survivor:
        async with isolated_test_database(test_database_url) as temporary:
            assert survivor.schema_name != temporary.schema_name
            assert len({survivor.banner_lock_id, survivor.rmp_lock_id,
                        temporary.banner_lock_id, temporary.rmp_lock_id}) == 4
            await asyncio.gather(
                _seed_records(survivor.session_factory, "survivor"),
                _seed_records(temporary.session_factory, "temporary"),
            )
            original = await _records(survivor.session_factory)
            assert original == {
                "title": "survivor", "section_location": "survivor",
                "meeting_location": "survivor", "department": "survivor",
                "error_message": "survivor", "rmp_data": {"label": "survivor"},
            }
            assert (await _records(temporary.session_factory))["title"] == "temporary"

            # Advisory locks belong to the database, not a schema: both tests
            # must be able to hold their own scraper locks at the same time.
            async with survivor.session_factory() as first, temporary.session_factory() as second:
                for session, database in ((first, survivor), (second, temporary)):
                    for lock_id in (database.banner_lock_id, database.rmp_lock_id):
                        async with advisory_lock(session, lock_id, "test") as acquired:
                            assert acquired is True

        assert not await _schema_exists(test_database_url, temporary.schema_name)
        assert await _records(survivor.session_factory) == original
    assert not await _schema_exists(test_database_url, survivor.schema_name)


@pytest.mark.asyncio
async def test_cleanup_preserves_unrelated_recent_public_scraper_runs(test_database_url):
    from tests.database_isolation import isolated_test_database

    engine = create_async_engine(test_database_url)
    record_id = None
    subject = f"isolation_{uuid4().hex}"
    try:
        async with engine.begin() as connection:
            record_id = await connection.scalar(text("""
                INSERT INTO public.scraper_runs (scraper, status, subject)
                VALUES ('banner', 'completed', :subject) RETURNING id
            """), {"subject": subject})

        async with isolated_test_database(test_database_url) as database:
            await _seed_records(database.session_factory, "temporary")

        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT subject FROM public.scraper_runs WHERE id = :id"),
                {"id": record_id},
            ) == subject
    finally:
        try:
            if record_id is not None:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("DELETE FROM public.scraper_runs WHERE id = :id AND subject = :subject"),
                        {"id": record_id, "subject": subject},
                    )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["assertion", "transaction", "cancellation"])
async def test_failed_test_still_removes_its_schema(test_database_url, failure):
    from tests.database_isolation import isolated_test_database

    schema_name = None

    async def failing_test():
        nonlocal schema_name
        async with isolated_test_database(test_database_url) as database:
            schema_name = database.schema_name
            await _seed_records(database.session_factory, "committed before failure")
            async with database.session_factory() as session:
                if failure == "transaction":
                    await session.execute(text("SELECT 1 / 0"))
                elif failure == "cancellation":
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)
                else:
                    raise AssertionError("Simulated failed test")

    expected = {"assertion": AssertionError, "transaction": DBAPIError,
                "cancellation": asyncio.CancelledError}[failure]
    with pytest.raises(expected):
        await asyncio.create_task(failing_test())
    assert schema_name is not None
    assert not await _schema_exists(test_database_url, schema_name)


@pytest.mark.asyncio
async def test_partial_schema_setup_is_rolled_back(test_database_url, monkeypatch):
    from tests import database_isolation

    identifier = uuid4()
    original_apply = database_isolation.apply_migrations

    async def failing_setup(connection, **kwargs):
        await original_apply(connection, **kwargs)
        await connection.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(database_isolation, "uuid4", lambda: identifier)
    monkeypatch.setattr(database_isolation, "apply_migrations", failing_setup)
    with pytest.raises(DBAPIError, match="division by zero"):
        async with database_isolation.isolated_test_database(test_database_url):
            pytest.fail("Failed migration setup must not yield a database")
    assert not await _schema_exists(test_database_url, f"test_{identifier.hex}")


@pytest.mark.asyncio
async def test_schema_name_collision_never_drops_existing_schema(test_database_url, monkeypatch):
    from tests import database_isolation

    identifier = uuid4()
    monkeypatch.setattr(database_isolation, "uuid4", lambda: identifier)
    async with database_isolation.isolated_test_database(test_database_url) as original:
        await _seed_records(original.session_factory, "keep")
        with pytest.raises(DBAPIError, match="already exists"):
            async with database_isolation.isolated_test_database(test_database_url):
                pytest.fail("An existing schema must not be reused")
        assert (await _records(original.session_factory))["title"] == "keep"


@pytest.mark.asyncio
async def test_missing_test_table_cannot_fall_back_to_public(test_database_url):
    from tests.database_isolation import isolated_test_database

    async with isolated_test_database(test_database_url) as database:
        async with database.session_factory() as session:
            assert await session.scalar(text("SELECT to_regclass('public.rmp_cache')")) is not None
            await session.execute(text("DROP TABLE rmp_cache"))
            await session.commit()
            with pytest.raises(DBAPIError, match="does not exist"):
                await session.execute(text("SELECT COUNT(*) FROM rmp_cache"))
