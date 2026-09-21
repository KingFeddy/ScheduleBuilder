"""Backfill and deferred cleanup safeguards on privately owned PostgreSQL data."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts.migrate import MigrationError, apply_migrations
from tests.database_isolation import isolated_test_database


API_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def database(test_database_url):
    async with isolated_test_database(test_database_url) as database:
        async with database.session_factory.begin() as session:
            await session.execute(text("INSERT INTO courses (course_code, title, credits) VALUES ('CS999', 'Test course', 3)"))
        yield database


async def _section(database, crn="10001", *, term="202690", days="MW", start=time(10), end=time(11)):
    async with database.session_factory.begin() as session:
        await session.execute(text("""
            INSERT INTO sections (crn, term, course_code, days, start_time, end_time, location, total_seats, open_seats)
            VALUES (:crn, :term, 'CS999', :days, :start, :end, 'CKB 101', 30, 10)
        """), {"crn": crn, "term": term, "days": days, "start": start, "end": end})


async def _meeting(database, crn="10001", *, term="202690", days="MW", start=time(10), end=time(11)):
    async with database.session_factory.begin() as session:
        await session.execute(text("""
            INSERT INTO meetings (crn, term, days, start_time, end_time)
            VALUES (:crn, :term, :days, :start, :end)
        """), {"crn": crn, "term": term, "days": days, "start": start, "end": end})


async def _rows(database):
    async with database.session_factory() as session:
        return (await session.execute(text("SELECT crn, term, days, start_time, end_time, location FROM meetings ORDER BY id"))).all()


async def _report(database, **kwargs):
    from scripts.backfill_meetings import inspect_coverage
    async with database.session_factory.begin() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        return await inspect_coverage(await session.connection(), schema_name=database.schema_name, **kwargs)


async def _apply(database, **kwargs):
    from scripts.backfill_meetings import apply_backfill
    async with database.session_factory.begin() as session:
        return await apply_backfill(await session.connection(), schema_name=database.schema_name, **kwargs)


@pytest.mark.asyncio
async def test_dry_run_lists_missing_meetings_without_writing(database):
    await _section(database)
    report = await _report(database)
    assert report.total_sections == 1
    assert len(report.candidates) == 1
    assert report.candidates[0]["crn"] == "10001"
    assert report.issues == []
    assert await _rows(database) == []


@pytest.mark.asyncio
async def test_backfill_is_exact_repeatable_and_term_scoped(database):
    await _section(database)
    await _section(database, term="202710")
    report = await _apply(database, term="202690")
    assert report.inserted == 1
    assert report.candidates == []
    assert (await _rows(database))[0] == ("10001", "202690", "MW", time(10), time(11), "CKB 101")
    assert (await _apply(database, term="202690")).inserted == 0
    assert (await _apply(database)).inserted == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("days, start, end", [
    (None, None, None),
    ("MW", time(10), None)
])
async def test_unresolved_legacy_rows_block_apply_without_partial_changes(database, days, start, end):
    from scripts.backfill_meetings import BackfillError
    await _section(database)
    await _section(database, "10002", days=days, start=start, end=end)
    assert (await _report(database)).issues
    with pytest.raises(BackfillError, match="unresolved"):
        await _apply(database)
    assert await _rows(database) == []


@pytest.mark.asyncio
async def test_explicit_async_rows_are_preserved_but_not_inferred_from_missing_data(database):
    await _section(database, days=None, start=None, end=None)
    await _meeting(database, days=None, start=None, end=None)
    assert (await _apply(database)).inserted == 0
    assert (await _report(database)).issues == []


@pytest.mark.asyncio
async def test_database_error_rolls_back_entire_backfill(database):
    await _section(database)
    await _section(database, "10002")
    async with database.session_factory.begin() as session:
        await session.execute(text("ALTER TABLE meetings ADD CONSTRAINT force_failure CHECK (crn <> '10002')"))
    with pytest.raises(DBAPIError, match="force_failure"):
        await _apply(database)
    assert await _rows(database) == []


async def _cleanup(database, *, since=None, note="Verified Banner multi-pattern production data", migrations=None):
    async with database.session_factory.begin() as session:
        return await apply_migrations(
            await session.connection(), schema_name=database.schema_name,
            migrations=migrations, cleanup_meetings=True,
            production_verified_since=since, production_verification_note=note,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("age, note", [
    (None, "Verified"),
    (15, "")
])
async def test_cleanup_requires_two_weeks_of_explicit_production_verification(database, age, note):
    await _section(database)
    await _meeting(database)
    since = datetime.now(timezone.utc) - timedelta(days=age) if age is not None else None
    with pytest.raises(MigrationError, match="[Vv]erif|[Pp]roduction|14 days"):
        await _cleanup(database, since=since, note=note)
    assert (await _report(database)).legacy_columns_present


@pytest.mark.asyncio
async def test_cleanup_rechecks_all_terms_and_preserves_columns_when_incomplete(database):
    await _section(database)
    await _meeting(database)
    assert not (await _report(database)).candidates
    await _section(database, term="202710")
    with pytest.raises(MigrationError, match="coverage"):
        await _cleanup(database, since=datetime.now(timezone.utc) - timedelta(days=15))
    assert (await _report(database)).legacy_columns_present


@pytest.mark.asyncio
async def test_verified_cleanup_records_008_preserves_meetings_and_is_repeatable(database):
    from scripts.verify_migrations import schema_errors
    await _section(database)
    await _meeting(database)
    original = await _rows(database)
    since = datetime.now(timezone.utc) - timedelta(days=15)
    applied = await _cleanup(database, since=since)
    assert [migration.version for migration in applied] == ["008"]
    assert await _rows(database) == original
    assert not (await _report(database)).legacy_columns_present
    assert await _cleanup(database, since=since) == []
    async with database.session_factory.begin() as session:
        assert await schema_errors(await session.connection(), schema_name=database.schema_name) == []


@pytest.mark.asyncio
async def test_cleanup_ddl_failure_restores_columns_and_does_not_record_008(database):
    await _section(database)
    await _meeting(database)
    async with database.session_factory.begin() as session:
        await session.execute(text("CREATE VIEW legacy_dependency AS SELECT start_time FROM sections"))
    with pytest.raises(MigrationError, match="008_drop_sections_deprecated_columns.sql"):
        await _cleanup(database, since=datetime.now(timezone.utc) - timedelta(days=15))
    assert (await _report(database)).legacy_columns_present
    async with database.session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM schema_migrations WHERE version = '008'")) == 0


@pytest.mark.asyncio
async def test_backfill_handles_legacy_database_without_creating_history(database):
    await _section(database)
    async with database.session_factory.begin() as session:
        await session.execute(text("DROP TABLE schema_migrations"))
    assert (await _apply(database)).inserted == 1
    async with database.session_factory() as session:
        assert await session.scalar(text("SELECT to_regclass('schema_migrations')")) is None
    with pytest.raises(MigrationError, match="no migration history"):
        await _cleanup(database, since=datetime.now(timezone.utc) - timedelta(days=15))


@pytest.mark.parametrize("url", [None])
def test_backfill_never_falls_back_to_application_configuration(url):
    environment = {**os.environ, "DATABASE_URL": "never-use-the-application-database"}
    environment.pop("BACKFILL_DATABASE_URL", None)
    if url is not None:
        environment["BACKFILL_DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "scripts.backfill_meetings"], cwd=API_ROOT,
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "BACKFILL_DATABASE_URL" in result.stderr
    assert "secret" not in result.stdout + result.stderr
    assert environment["DATABASE_URL"] not in result.stdout + result.stderr
