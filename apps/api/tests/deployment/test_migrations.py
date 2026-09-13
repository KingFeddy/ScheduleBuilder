"""Exercise the versioned SQL migration path against disposable PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.database_isolation import isolated_test_database


API_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_VERSIONS = ["000", "007", "009", "012", "013", "014"]


@pytest_asyncio.fixture
async def empty_database(test_database_url):
    async with isolated_test_database(test_database_url, initialize=False) as database:
        yield database


@pytest.fixture
def migration_directory(tmp_path):
    return Path(shutil.copytree(API_ROOT / "migrations", tmp_path / "migrations"))


async def _apply(database, migrations=None):
    from scripts.migrate import apply_migrations

    async with database.session_factory.begin() as session:
        return await apply_migrations(
            await session.connection(), schema_name=database.schema_name, migrations=migrations,
        )


async def _history(database):
    async with database.session_factory() as session:
        return (await session.execute(text("""
            SELECT version, filename, checksum, applied_at
            FROM schema_migrations ORDER BY version
        """))).all()


async def _tables(database):
    async with database.session_factory() as session:
        return (await session.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = current_schema() ORDER BY tablename
        """))).scalars().all()


def _append_migration(directory, sql):
    filename = "999_test_migration.sql"
    (directory / filename).write_text(sql)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["migrations"].append({"file": filename})
    manifest_path.write_text(json.dumps(manifest))


@pytest.mark.asyncio
async def test_database_fixtures_are_built_from_recorded_migrations(test_database_url):
    async with isolated_test_database(test_database_url) as database:
        async with database.session_factory() as session:
            assert await session.scalar(text("SELECT to_regclass('schema_migrations')")) is not None
            versions = (await session.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
            )).scalars().all()
            assert versions == ACTIVE_VERSIONS

            columns = (await session.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'sections'
            """))).scalars().all()
            assert {"section_number", "days", "start_time", "end_time"} <= set(columns)


@pytest.mark.asyncio
async def test_repeated_migrations_preserve_data_and_history(empty_database):
    applied = await _apply(empty_database)
    assert [migration.version for migration in applied] == ACTIVE_VERSIONS
    original = await _history(empty_database)
    assert all(len(row.checksum) == 64 and row.applied_at is not None for row in original)
    async with empty_database.session_factory.begin() as session:
        await session.execute(text("""
            INSERT INTO courses (course_code, title, credits)
            VALUES ('CS999', 'Preserve me', 3)
        """))

    assert await _apply(empty_database) == []
    assert await _history(empty_database) == original
    async with empty_database.session_factory() as session:
        assert await session.scalar(text("SELECT title FROM courses")) == "Preserve me"


@pytest.mark.asyncio
async def test_prerequisite_verification_upgrade_preserves_legacy_arrays_as_unverified(empty_database):
    from scripts.migrate import load_migrations

    await _apply(empty_database, [m for m in load_migrations() if m.version < "014"])
    async with empty_database.session_factory.begin() as session:
        await session.execute(text("""
            INSERT INTO courses (course_code, title, credits, prerequisites)
            VALUES ('CS999', 'Known prerequisites', 3, ARRAY['CS100', 'MATH111']),
                   ('CS998', 'Unknown prerequisites', 4, '{}')
        """))
    assert [m.version for m in await _apply(empty_database)] == ["014"]
    async with empty_database.session_factory() as session:
        courses = (await session.execute(text("SELECT * FROM courses ORDER BY course_code"))).mappings().all()
        assert [c["prerequisites"] for c in courses] == [[], ["CS100", "MATH111"]]
        assert [c["title"] for c in courses] == ["Unknown prerequisites", "Known prerequisites"]
        for course in courses:
            assert course["prerequisites_status"] == "unverified"
            assert course["prerequisites_attempted_at"] is None
            assert course["prerequisites_verified_at"] is None
            assert course["prerequisites_error"] is None
    assert await _apply(empty_database) == []


@pytest.mark.asyncio
async def test_concurrent_migrators_apply_each_version_once(empty_database):
    first, second = await asyncio.gather(_apply(empty_database), _apply(empty_database))
    assert sorted([len(first), len(second)]) == [0, len(ACTIVE_VERSIONS)]
    assert [row.version for row in await _history(empty_database)] == ACTIVE_VERSIONS


@pytest.mark.asyncio
async def test_failed_batch_rolls_back_ddl_and_history_then_can_retry(empty_database, migration_directory):
    from scripts.migrate import MigrationError, load_migrations

    _append_migration(migration_directory, "CREATE TABLE must_rollback (id INTEGER); SELECT 1 / 0;")
    with pytest.raises(MigrationError, match="999_test_migration.sql"):
        await _apply(empty_database, load_migrations(migration_directory))
    assert await _tables(empty_database) == []

    (migration_directory / "999_test_migration.sql").write_text("CREATE TABLE retry_succeeded (id INTEGER);")
    applied = await _apply(empty_database, load_migrations(migration_directory))
    assert [migration.version for migration in applied] == [*ACTIVE_VERSIONS, "999"]
    assert "retry_succeeded" in await _tables(empty_database)
    assert "must_rollback" not in await _tables(empty_database)


@pytest.mark.asyncio
async def test_edited_applied_migration_is_rejected_before_pending_ddl(empty_database, migration_directory):
    from scripts.migrate import MigrationError, load_migrations

    await _apply(empty_database)
    original = await _history(empty_database)
    script = migration_directory / "009_courses_prerequisites.sql"
    script.write_text(script.read_text() + "\n-- Editing applied SQL is forbidden.\n")
    _append_migration(migration_directory, "CREATE TABLE must_not_run (id INTEGER);")
    with pytest.raises(MigrationError, match="009.*changed"):
        await _apply(empty_database, load_migrations(migration_directory))
    assert await _history(empty_database) == original
    assert "must_not_run" not in await _tables(empty_database)


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["unknown_version", "missing_version", "renamed_file"])
async def test_inconsistent_history_is_rejected(empty_database, problem):
    from scripts.migrate import MigrationError

    await _apply(empty_database)
    async with empty_database.session_factory.begin() as session:
        if problem == "unknown_version":
            await session.execute(text("""
                INSERT INTO schema_migrations (version, filename, checksum)
                VALUES ('999', '999_unknown.sql', repeat('0', 64))
            """))
        elif problem == "missing_version":
            await session.execute(text("DELETE FROM schema_migrations WHERE version = '007'"))
        else:
            await session.execute(text("""
                UPDATE schema_migrations SET filename = '009_wrong_name.sql' WHERE version = '009'
            """))
    original = await _history(empty_database)
    expected = {"unknown_version": "unknown", "missing_version": "gap", "renamed_file": "changed"}[problem]
    with pytest.raises(MigrationError, match=expected):
        await _apply(empty_database)
    assert await _history(empty_database) == original


@pytest.mark.asyncio
async def test_unmanaged_existing_schema_is_not_adopted_or_modified(empty_database):
    from scripts.migrate import MigrationError

    async with empty_database.session_factory.begin() as session:
        await session.execute(text("CREATE TABLE courses (title TEXT)"))
        await session.execute(text("INSERT INTO courses VALUES ('Existing data')"))
    with pytest.raises(MigrationError, match="no migration history"):
        await _apply(empty_database)
    assert await _tables(empty_database) == ["courses"]
    async with empty_database.session_factory() as session:
        assert await session.scalar(text("SELECT title FROM courses")) == "Existing data"


@pytest.mark.asyncio
async def test_cli_status_is_read_only_and_apply_uses_the_real_chain(empty_database, test_database_url):
    async def command(action):
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "scripts.migrate", action, "--schema", empty_database.schema_name],
            cwd=API_ROOT,
            env={**os.environ, "MIGRATION_DATABASE_URL": test_database_url},
            capture_output=True, text=True, timeout=30,
        )

    status = await command("status")
    assert status.returncode == 0, status.stderr
    assert "000 PENDING" in status.stdout
    assert "008 DEFERRED" in status.stdout
    assert await _tables(empty_database) == []

    applied = await command("apply")
    assert applied.returncode == 0, applied.stderr
    assert [row.version for row in await _history(empty_database)] == ACTIVE_VERSIONS
    status = await command("status")
    assert status.returncode == 0, status.stderr
    assert "014 APPLIED" in status.stdout
    assert "008 DEFERRED" in status.stdout
    assert "PENDING" not in status.stdout


@pytest.mark.parametrize("problem", ["missing_file", "unlisted_file", "duplicate", "out_of_order", "path_escape", "unknown_key"])
def test_invalid_manifests_are_rejected(migration_directory, problem):
    from scripts.migrate import MigrationError, load_migrations

    manifest_path = migration_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entries = manifest["migrations"]
    if problem == "missing_file":
        (migration_directory / entries[0]["file"]).unlink()
    elif problem == "unlisted_file":
        (migration_directory / "999_unlisted.sql").write_text("SELECT 1;")
    elif problem == "duplicate":
        entries.insert(1, dict(entries[0]))
    elif problem == "out_of_order":
        entries[0], entries[1] = entries[1], entries[0]
    elif problem == "path_escape":
        entries[0]["file"] = "../000_baseline.sql"
    else:
        entries[2]["defered_reason"] = entries[2].pop("deferred_reason")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(MigrationError):
        load_migrations(migration_directory)


@pytest.mark.parametrize("migration_url", [None, "invalid-secret-url", "sqlite:///secret.db"])
def test_cli_requires_explicit_postgres_configuration_and_redacts_it(migration_url):
    environment = {**os.environ, "DATABASE_URL": "never-use-the-application-database"}
    environment.pop("MIGRATION_DATABASE_URL", None)
    if migration_url is not None:
        environment["MIGRATION_DATABASE_URL"] = migration_url
    result = subprocess.run(
        [sys.executable, "-m", "scripts.migrate", "apply"],
        cwd=API_ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL" in result.stderr
    assert "secret" not in result.stdout + result.stderr
    assert environment["DATABASE_URL"] not in result.stdout + result.stderr
