"""Apply the repository's ordered SQL migrations in one PostgreSQL transaction.

CLI: MIGRATION_DATABASE_URL=... python -m scripts.migrate status|apply
The application environment and dotenv are deliberately not imported.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from sqlalchemy import make_url, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[1] / "migrations"
# Two-integer advisory locks use a separate key space from the scraper's bigint
# locks. A schema hash lets independent test schemas migrate concurrently.
MIGRATION_LOCK_NAMESPACE = 1935830381


class MigrationError(ValueError):
    """Migration files or the database's recorded history cannot be trusted."""


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str
    checksum: str
    sql: str
    deferred_reason: str | None = None


def load_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> list[Migration]:
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except (OSError, ValueError) as error:
        raise MigrationError("Cannot read the migration manifest.") from error
    if not isinstance(manifest, dict) or set(manifest) != {"migrations"}:
        raise MigrationError("The migration manifest must contain only a migrations list.")
    entries = manifest["migrations"]
    if not isinstance(entries, list) or not entries:
        raise MigrationError("The migration manifest must contain a nonempty migrations list.")

    migrations = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"file", "deferred_reason"}:
            raise MigrationError("Invalid migration manifest entry.")
        filename = entry.get("file")
        match = re.fullmatch(r"([0-9]{3})_[a-z0-9_]+\.sql", filename) if isinstance(filename, str) else None
        if match is None:
            raise MigrationError("Migration filenames must use NNN_description.sql without a path.")
        reason = entry.get("deferred_reason")
        if "deferred_reason" in entry and (not isinstance(reason, str) or not reason.strip()):
            raise MigrationError(f"Deferred migration {filename} needs a reason.")
        try:
            contents = (directory / filename).read_bytes()
            sql = contents.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise MigrationError(f"Cannot read migration {filename}.") from error
        migrations.append(Migration(match[1], filename, hashlib.sha256(contents).hexdigest(), sql, reason))

    versions = [migration.version for migration in migrations]
    if versions != sorted(set(versions)):
        raise MigrationError("Migration versions must be unique and listed in increasing order.")
    if {path.name for path in directory.glob("*.sql")} != {m.filename for m in migrations}:
        raise MigrationError("Every SQL migration must be listed in the manifest.")
    return migrations


async def _select_schema(connection: AsyncConnection, schema_name: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema_name) or schema_name.startswith("pg_"):
        raise MigrationError("Use an ordinary lowercase PostgreSQL schema name.")
    exists = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"),
        {"schema": schema_name},
    )
    if not exists:
        raise MigrationError("The target schema does not exist; create it before migrating.")
    await connection.execute(
        text("SELECT set_config('search_path', :schema, true)"), {"schema": schema_name},
    )


async def _read_history(connection: AsyncConnection, schema_name: str):
    kind = await connection.scalar(text("""
        SELECT c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relname = 'schema_migrations'
    """), {"schema": schema_name})
    if kind is None:
        populated = await connection.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema
            )
        """), {"schema": schema_name})
        if populated:
            raise MigrationError(
                "The target schema contains existing relations but no migration history. "
                "Automatic adoption is refused; reconcile it before using this runner."
            )
        return None
    if kind != "r":
        raise MigrationError("schema_migrations must be an ordinary history table.")
    return (await connection.execute(
        text("SELECT version, filename, checksum FROM schema_migrations ORDER BY version")
    )).mappings().all()


def _validate_history(migrations: list[Migration], history) -> set[str]:
    known = {migration.version: migration for migration in migrations}
    applied = set()
    for row in history or []:
        version = row["version"]
        if version not in known or version in applied:
            raise MigrationError("Migration history contains an unknown or duplicate version.")
        migration = known[version]
        if row["filename"] != migration.filename or row["checksum"] != migration.checksum:
            raise MigrationError(f"Applied migration {version} has changed; restore its original file.")
        applied.add(version)
    active = [migration.version for migration in migrations if migration.deferred_reason is None]
    applied_active = [version for version in active if version in applied]
    if applied_active != active[:len(applied_active)]:
        raise MigrationError("Migration history has a gap before an applied active migration.")
    return applied


async def migration_status(
    connection: AsyncConnection, *, schema_name: str = "public", migrations: list[Migration] | None = None,
) -> list[tuple[Migration, str]]:
    """Inspect without creating history or modifying the schema."""
    migrations = load_migrations() if migrations is None else migrations
    await _select_schema(connection, schema_name)
    applied = _validate_history(migrations, await _read_history(connection, schema_name))
    return [
        (migration, "applied" if migration.version in applied else
         "deferred" if migration.deferred_reason is not None else "pending")
        for migration in migrations
    ]


async def apply_migrations(
    connection: AsyncConnection, *, schema_name: str = "public", migrations: list[Migration] | None = None,
) -> list[Migration]:
    """Apply pending SQL inside the caller's READ COMMITTED transaction.

    The caller must commit on success and roll back on failure. All migration
    files must contain transactional SQL, without their own transaction controls.
    """
    if not connection.in_transaction():
        raise MigrationError("Migration application requires an enclosing transaction.")
    migrations = load_migrations() if migrations is None else migrations
    await _select_schema(connection, schema_name)
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:schema))"),
        {"namespace": MIGRATION_LOCK_NAMESPACE, "schema": schema_name},
    )
    # Inspect only after acquiring the lock: another runner may have committed
    # while this connection waited. READ COMMITTED sees its completed history.
    history = await _read_history(connection, schema_name)
    applied = _validate_history(migrations, history)
    if history is None:
        await connection.execute(text("""
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

    pending = [m for m in migrations if m.version not in applied and m.deferred_reason is None]
    raw_connection = await connection.get_raw_connection()
    for migration in pending:
        try:
            # Execute each complete SQL file using asyncpg's simple-query
            # protocol, in this same SQLAlchemy-managed transaction.
            await raw_connection.driver_connection.execute(migration.sql)
            await connection.execute(text("""
                INSERT INTO schema_migrations (version, filename, checksum)
                VALUES (:version, :filename, :checksum)
            """), {"version": migration.version, "filename": migration.filename, "checksum": migration.checksum})
        except Exception as error:
            raise MigrationError(f"Migration {migration.filename} failed; roll back the transaction.") from error
    return pending


async def _run(action: str, database_url: str, schema_name: str) -> None:
    migrations = load_migrations()
    engine = create_async_engine(database_url, isolation_level="READ COMMITTED", connect_args={"timeout": 10})
    try:
        async with engine.begin() as connection:
            if action == "status":
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                for migration, state in await migration_status(connection, schema_name=schema_name, migrations=migrations):
                    reason = f" — {migration.deferred_reason}" if state == "deferred" else ""
                    print(f"{migration.version} {state.upper()} {migration.filename}{reason}")
            else:
                applied = await apply_migrations(connection, schema_name=schema_name, migrations=migrations)
        # Only report applied changes after the transaction has committed.
        if action == "apply":
            for migration in applied:
                print(f"APPLIED {migration.filename}")
            if not applied:
                print("No pending active migrations.")
            for migration in migrations:
                if migration.deferred_reason is not None:
                    print(f"DEFERRED {migration.filename}: {migration.deferred_reason}")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "apply"))
    parser.add_argument("--schema", default="public", help="Existing target schema (default: public)")
    args = parser.parse_args()
    database_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        url = make_url(database_url or "")
        valid = url.drivername == "postgresql+asyncpg" and bool(url.host) and bool(url.database)
    except (ArgumentError, ValueError, TypeError):
        valid = False
    if not valid:
        print("Set MIGRATION_DATABASE_URL explicitly to a postgresql+asyncpg URL with a host and database.", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(args.action, database_url, args.schema))
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # Driver and URL errors can contain credentials; do not echo them.
        print("Migration command failed. Check database access and the migration SQL.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
