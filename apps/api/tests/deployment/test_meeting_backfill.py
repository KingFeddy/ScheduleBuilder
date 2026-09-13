"""Backfill and deferred cleanup safeguards on privately owned PostgreSQL data."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts.migrate import MigrationError, apply_migrations, load_migrations
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
async def test_existing_split_patterns_are_preserved_without_flat_duplicate(database):
    await _section(database)
    await _meeting(database, days="M")
    await _meeting(database, days="W")
    original = await _rows(database)
    assert (await _apply(database)).inserted == 0
    assert await _rows(database) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("days, start, end", [
    (None, None, None), ("MW", None, None), ("MW", time(10), None),
    (None, time(10), time(11)), ("X", time(10), time(11)), ("MW", time(11), time(10)),
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
@pytest.mark.parametrize("days, start, end", [(None, None, None), ("M", time(10), time(11)), ("MW", time(14), time(15))])
async def test_nonzero_meeting_count_cannot_hide_incomplete_time_coverage(database, days, start, end):
    from scripts.backfill_meetings import BackfillError
    await _section(database)
    await _meeting(database, days=days, start=start, end=end)
    original = await _rows(database)
    report = await _report(database)
    assert report.meeting_count == 1
    assert report.issues
    with pytest.raises(BackfillError, match="unresolved"):
        await _apply(database)
    assert await _rows(database) == original


@pytest.mark.asyncio
async def test_explicit_async_rows_are_preserved_but_not_inferred_from_missing_data(database):
    await _section(database, days=None, start=None, end=None)
    await _meeting(database, days=None, start=None, end=None)
    assert (await _apply(database)).inserted == 0
    assert (await _report(database)).issues == []


@pytest.mark.asyncio
async def test_concurrent_backfills_insert_once(database):
    await _section(database)
    reports = await asyncio.gather(_apply(database), _apply(database))
    assert sorted(report.inserted for report in reports) == [0, 1]
    assert len(await _rows(database)) == 1


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
    (None, "Verified"), (7, "Verified"), (-1, "Verified"), (15, ""),
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
async def test_empty_database_cannot_satisfy_cleanup_coverage(database):
    with pytest.raises(MigrationError, match="coverage"):
        await _cleanup(database, since=datetime.now(timezone.utc) - timedelta(days=15))


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
async def test_undefering_008_in_manifest_does_not_bypass_cleanup_guard(database):
    # Build only the base and meetings migrations so history-prefix validation
    # cannot mask a missing data guard when 008 is made active accidentally.
    async with database.session_factory.begin() as session:
        await session.execute(text("DELETE FROM schema_migrations WHERE version IN ('009', '012', '013')"))
        migrations = [replace(m, deferred_reason=None) if m.version == "008" else m for m in load_migrations()]
        with pytest.raises(MigrationError, match="cleanup-meetings"):
            await apply_migrations(await session.connection(), schema_name=database.schema_name, migrations=migrations)


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
async def test_cleanup_holds_coverage_lock_until_transaction_finishes(database):
    from scripts.backfill_meetings import guard_meetings_cleanup
    await _section(database)
    await _meeting(database)
    async with database.session_factory.begin() as session:
        await guard_meetings_cleanup(
            await session.connection(), schema_name=database.schema_name,
            production_verified_since=datetime.now(timezone.utc) - timedelta(days=15),
            production_verification_note="Reviewed production patterns and history",
        )
        async with database.session_factory.begin() as writer:
            await writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await writer.execute(text("DELETE FROM meetings"))
    assert len(await _rows(database)) == 1


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


@pytest.mark.asyncio
async def test_api_reports_incomplete_migration_then_returns_timed_schedule_after_backfill(database):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from src.dependencies import get_db

    await _section(database)
    async def override_db():
        async with database.session_factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post("/api/schedule/solve", json={"course_codes": ["CS999"], "term": "202690"})
            assert missing.status_code == 503
            assert "Meeting data is incomplete" in missing.json()["detail"]
            missing_sections = await client.get("/api/courses/CS999/sections", params={"term": "202690"})
            assert missing_sections.status_code == 503
            await _apply(database)
            valid = await client.post("/api/schedule/solve", json={"course_codes": ["CS999"], "term": "202690"})
            assert valid.status_code == 200
            result = valid.json()["results"][0]
            assert result["has_async_sections"] is False
            assert result["sections"][0]["meetings"][0]["start_time"] == "10:00"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_cleanup_cli_requires_evidence_and_preview_cannot_drop_columns(database, test_database_url):
    await _section(database)
    await _meeting(database)
    since = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    async def command(module, *arguments):
        return await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", module, *arguments, "--schema", database.schema_name],
            cwd=API_ROOT, env={**os.environ, "BACKFILL_DATABASE_URL": test_database_url, "MIGRATION_DATABASE_URL": test_database_url},
            capture_output=True, text=True, timeout=30,
        )
    blocked = await command("scripts.migrate", "cleanup-meetings")
    assert blocked.returncode == 1
    assert "14 days" in blocked.stderr
    evidence = ["--production-verified-since", since, "--production-verification-note", "Reviewed production patterns and history"]
    preview = await command("scripts.backfill_meetings", "--check-cleanup", *evidence)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["cleanup_ready"] is True
    assert (await _report(database)).legacy_columns_present
    scoped = await command("scripts.backfill_meetings", "--check-cleanup", "--term", "202690", *evidence)
    assert scoped.returncode == 2
    applied = await command("scripts.migrate", "cleanup-meetings", *evidence)
    assert applied.returncode == 0, applied.stderr
    assert "APPLIED 008_drop_sections_deprecated_columns.sql" in applied.stdout
    assert "DEFERRED 008" not in applied.stdout
    assert not (await _report(database)).legacy_columns_present


@pytest.mark.asyncio
async def test_default_cli_is_read_only_and_apply_requires_explicit_selection(database, test_database_url):
    await _section(database)
    async def command(*arguments):
        return await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "scripts.backfill_meetings", "--schema", database.schema_name, *arguments],
            cwd=API_ROOT, env={**os.environ, "BACKFILL_DATABASE_URL": test_database_url},
            capture_output=True, text=True, timeout=30,
        )
    preview = await command()
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["would_insert"] == 1
    assert await _rows(database) == []
    applied = await command("--apply")
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["inserted"] == 1


@pytest.mark.parametrize("url", [None, "invalid-secret-url", "sqlite:///secret.db"])
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
