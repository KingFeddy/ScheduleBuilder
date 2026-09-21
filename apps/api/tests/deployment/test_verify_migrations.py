"""Deployment must reject schema drift in a real, privately owned database schema."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.database_isolation import isolated_test_database


API_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def database(test_database_url):
    async with isolated_test_database(test_database_url) as database:
        yield database


async def _change(database, sql):
    async with database.session_factory.begin() as session:
        await session.execute(text(sql))


async def _errors(database):
    from scripts.verify_migrations import schema_errors

    async with database.session_factory.begin() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        return await schema_errors(await session.connection(), schema_name=database.schema_name)


@pytest.mark.asyncio
async def test_current_migrations_pass_without_touching_records(database):
    await _change(database, "INSERT INTO courses VALUES ('CS999', 'Keep this record', 3, '{}')")
    assert await _errors(database) == []
    async with database.session_factory() as session:
        assert await session.scalar(text("SELECT title FROM courses")) == "Keep this record"
        assert await session.scalar(text("SELECT count(*) FROM schema_migrations")) == 9


# Representative columns exercise missing-field detection across catalog tables.

@pytest.mark.asyncio
@pytest.mark.parametrize("table, column", [
    ('courses', 'credits_source'),
    ('sections', 'section_number'),
    ('meetings', 'start_time'),
])
async def test_missing_runtime_column_is_named(database, table, column):
    # Identifiers come exclusively from the static parameter list above.
    await _change(database, f'ALTER TABLE "{table}" DROP COLUMN "{column}" CASCADE')
    assert f"MISSING COLUMN: {table}.{column}" in await _errors(database)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ['sections'])
async def test_public_table_cannot_replace_missing_target_table(database, table):
    await _change(database, f'DROP TABLE "{table}" CASCADE')
    assert any(error.startswith(f"MISSING TABLE: {table} ") for error in await _errors(database))


@pytest.mark.asyncio
@pytest.mark.parametrize("sql, detail", [
    ("ALTER TABLE sections ALTER COLUMN section_number TYPE integer USING NULL", "TYPE: sections.section_number"),
    ("ALTER TABLE courses ALTER COLUMN credits SET NOT NULL", "NULLABILITY: courses.credits"),
    ("ALTER TABLE courses ALTER COLUMN prerequisites DROP DEFAULT", "DEFAULT: courses.prerequisites"),
    ("ALTER TABLE scraper_runs ALTER COLUMN duration_ms DROP EXPRESSION", "GENERATED: scraper_runs.duration_ms"),
    ("ALTER TABLE sections DROP CONSTRAINT sections_pkey CASCADE", "PRIMARY KEY: sections"),
    ("ALTER TABLE meetings DROP CONSTRAINT meetings_time_pair", "CHECK: meetings.time_pair"),
    ("DROP INDEX idx_scraper_runs_term_recent", "INDEX: scraper_runs")
])
async def test_incompatible_schema_is_rejected(database, sql, detail):
    await _change(database, sql)
    assert any(detail in error for error in await _errors(database))


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [
    "FOREIGN KEY (crn, term) REFERENCES sections(crn, term)",
    "FOREIGN KEY (crn, term) REFERENCES sections(crn, term) ON DELETE CASCADE NOT VALID"
])
async def test_foreign_key_must_cascade_and_be_validated(database, replacement):
    await _change(database, "ALTER TABLE meetings DROP CONSTRAINT meetings_crn_term_fkey")
    await _change(database, "ALTER TABLE meetings ADD CONSTRAINT replacement " + replacement)
    assert any("FOREIGN KEY: meetings" in error for error in await _errors(database))


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [
    "CHECK (true)",
    "CHECK (status IN ('running', 'completed', 'failed', 'blocked', 'schema_change', 'skipped_overlap')) NOT VALID"
])
async def test_check_name_cannot_hide_weakened_or_unvalidated_rule(database, replacement):
    await _change(database, "ALTER TABLE scraper_runs DROP CONSTRAINT scraper_runs_status_check")
    await _change(database, "ALTER TABLE scraper_runs ADD CONSTRAINT scraper_runs_status_check " + replacement)
    assert any("CHECK: scraper_runs.status" in error for error in await _errors(database))


@pytest.mark.asyncio
async def test_deferrable_unique_constraint_cannot_support_scraper_upserts(database):
    await _change(database, "ALTER TABLE meetings DROP CONSTRAINT meetings_crn_term_days_start_time_end_time_key")
    await _change(database, "ALTER TABLE meetings ADD UNIQUE (crn, term, days, start_time, end_time) DEFERRABLE")
    assert any("UNIQUE: meetings" in error for error in await _errors(database))


@pytest.mark.asyncio
async def test_partial_unique_index_cannot_support_unconditional_upserts(database):
    await _change(database, "ALTER TABLE meetings DROP CONSTRAINT meetings_crn_term_days_start_time_end_time_key")
    await _change(database, "CREATE UNIQUE INDEX partial_key ON meetings(crn, term, days, start_time, end_time) WHERE days IS NOT NULL")
    assert any("UNIQUE: meetings" in error for error in await _errors(database))


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "DELETE FROM schema_migrations WHERE version = '013'",
    "UPDATE schema_migrations SET checksum = repeat('0', 64) WHERE version = '013'",
])
async def test_existing_migration_history_must_be_current_and_unchanged(database, sql):
    await _change(database, sql)
    assert any("MIGRATION HISTORY:" in error for error in await _errors(database))


@pytest.mark.asyncio
async def test_cli_verifier_enforces_read_only_and_disposes_on_failure(database, test_database_url, monkeypatch):
    from scripts import verify_migrations

    engine = verify_migrations.create_async_engine(test_database_url)
    dispose = AsyncMock(wraps=engine.dispose)
    observed_engine = MagicMock()
    observed_engine.begin = engine.begin
    observed_engine.dispose = dispose
    monkeypatch.setattr(verify_migrations, "create_async_engine", lambda *args, **kwargs: observed_engine)

    async def attempted_write(connection, **kwargs):
        # The wrapper must enforce read-only even if a future inspection changes.
        await connection.execute(
            text(f'INSERT INTO "{database.schema_name}".courses (course_code, title, credits) VALUES (:code, :title, :credits)'),
            {"code": "CS999", "title": "Unexpected", "credits": 3},
        )
        return []

    monkeypatch.setattr(verify_migrations, "schema_errors", attempted_write)
    with pytest.raises(DBAPIError, match="read-only transaction"):
        await verify_migrations.verify(test_database_url, schema_name=database.schema_name)
    dispose.assert_awaited_once()
    async with database.session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM courses")) == 0


@pytest.mark.parametrize("database_url", [None, "invalid-secret-url", "sqlite:///secret.db"])
def test_cli_requires_explicit_database_url_without_echoing_credentials(database_url, tmp_path):
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql+asyncpg://secret:secret@localhost/secret\n")
    result = subprocess.run(
        [sys.executable, str(API_ROOT / "scripts/verify_migrations.py")], cwd=tmp_path,
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "secret" not in result.stdout + result.stderr


def test_cli_connection_failure_is_nonzero_and_redacted(monkeypatch, capsys):
    from scripts import verify_migrations

    secret_url = "postgresql+asyncpg://user:secret-password@localhost:5432/database"
    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setattr(sys, "argv", ["verify_migrations"])
    monkeypatch.setattr(verify_migrations, "verify", AsyncMock(side_effect=RuntimeError(secret_url)))
    assert verify_migrations.main() == 1
    output = capsys.readouterr()
    assert "could not complete" in output.err
    assert "secret-password" not in output.out + output.err
