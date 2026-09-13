"""Read-only deployment gate for the complete runtime PostgreSQL schema.

DATABASE_URL=... python -m scripts.verify_migrations [--schema public]
This command never imports application settings or loads a dotenv file.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import make_url, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

if __package__:
    from .migrate import MigrationError, migration_status
    from .runtime_schema import CHECKS, COLUMNS, FOREIGN_KEYS, INDEXES, PRIMARY_KEYS, UNIQUE_KEYS
else:  # Preserve the existing python scripts/verify_migrations.py entry point.
    from migrate import MigrationError, migration_status
    from runtime_schema import CHECKS, COLUMNS, FOREIGN_KEYS, INDEXES, PRIMARY_KEYS, UNIQUE_KEYS


def _expression(value: str | None) -> str:
    """Remove whitespace outside quoted SQL tokens, preserving literal values."""
    return re.sub(
        r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\s+",
        lambda match: "" if match[0].isspace() else match[0], value or "",
    )


async def schema_errors(connection: AsyncConnection, *, schema_name: str = "public") -> list[str]:
    """Inspect catalog metadata only; callers can enforce a read-only transaction."""
    if not await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = :schema)"),
        {"schema": schema_name},
    ):
        return [f"MISSING SCHEMA: {schema_name}"]

    # Stable deparsing and no accidental resolution through public/user objects.
    await connection.execute(text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)"))
    parameters = {"schema": schema_name}
    relations = dict((await connection.execute(text("""
        SELECT c.relname, c.relkind::text
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema
    """), parameters)).all())
    columns = (await connection.execute(text("""
        SELECT c.relname AS table_name, a.attname AS column_name,
               format_type(a.atttypid, a.atttypmod) AS sql_type, a.attnotnull,
               a.attidentity::text AS identity, a.attgenerated::text AS generated,
               pg_get_expr(d.adbin, d.adrelid) AS expression,
               EXISTS (
                   SELECT 1 FROM pg_depend dep JOIN pg_class seq ON seq.oid = dep.refobjid
                   WHERE dep.classid = 'pg_attrdef'::regclass AND dep.objid = d.oid
                     AND dep.refclassid = 'pg_class'::regclass AND seq.relkind = 'S'
               ) AS sequence_default
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = :schema
    """), parameters)).mappings().all()
    constraints = (await connection.execute(text("""
        SELECT t.relname AS table_name, co.contype::text AS kind,
               co.convalidated AS validated, co.condeferrable AS deferrable,
               ARRAY(SELECT a.attname::text FROM unnest(co.conkey) WITH ORDINALITY k(num, ord)
                     JOIN pg_attribute a ON a.attrelid = co.conrelid AND a.attnum = k.num
                     ORDER BY k.ord) AS columns,
               rn.nspname AS referred_schema, rt.relname AS referred_table,
               ARRAY(SELECT a.attname::text FROM unnest(co.confkey) WITH ORDINALITY k(num, ord)
                     JOIN pg_attribute a ON a.attrelid = co.confrelid AND a.attnum = k.num
                     ORDER BY k.ord) AS referred_columns,
               co.confdeltype::text AS on_delete,
               pg_get_expr(co.conbin, co.conrelid) AS expression,
               i.indisvalid AS index_valid, i.indisready AS index_ready
        FROM pg_constraint co JOIN pg_class t ON t.oid = co.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        LEFT JOIN pg_class rt ON rt.oid = co.confrelid
        LEFT JOIN pg_namespace rn ON rn.oid = rt.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = co.conindid
        WHERE n.nspname = :schema
    """), parameters)).mappings().all()
    indexes = (await connection.execute(text("""
        SELECT t.relname AS table_name, i.indisunique AS is_unique,
               i.indimmediate AS immediate,
               ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num, ord)
                     LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
                     WHERE k.ord <= i.indnkeyatts ORDER BY k.ord) AS columns
        FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_class ix ON ix.oid = i.indexrelid JOIN pg_am am ON am.oid = ix.relam
        WHERE n.nspname = :schema AND i.indisvalid AND i.indisready
          AND i.indpred IS NULL AND i.indexprs IS NULL AND am.amname = 'btree'
    """), parameters)).mappings().all()

    errors = []
    by_column = {(row["table_name"], row["column_name"]): row for row in columns}
    present_tables = {table for table in COLUMNS if relations.get(table) in {"r", "p"}}
    for table, required in COLUMNS.items():
        if table not in present_tables:
            errors.append(f"MISSING TABLE: {table} (an ordinary or partitioned table is required)")
            continue
        for name, expected in required.items():
            actual = by_column.get((table, name))
            field = f"{table}.{name}"
            if actual is None:
                errors.append(f"MISSING COLUMN: {field}")
                continue
            if actual["sql_type"] != expected.sql_type:
                errors.append(f"TYPE: {field} must be {expected.sql_type}; found {actual['sql_type']}")
            if actual["attnotnull"] != (not expected.nullable):
                errors.append(f"NULLABILITY: {field} must {'allow NULL' if expected.nullable else 'be NOT NULL'}")
            expression = _expression(actual["expression"])
            if expected.generated:
                if actual["generated"] != "s" or expression != _expression(expected.generated):
                    errors.append(f"GENERATED: {field} must use the stored runtime expression")
            elif actual["generated"]:
                errors.append(f"GENERATED: {field} must be writable")
            if expected.default == "sequence":
                serial = actual["sequence_default"] and re.fullmatch(r"nextval\('(?:[^']|'')+'::regclass\)", expression)
                if not actual["identity"] and not serial:
                    errors.append(f"DEFAULT: {field} must generate IDs from a sequence or identity")
            elif expected.default is not None:
                allowed = {_expression(expected.default)}
                if expected.default == "now()":
                    allowed.update({"CURRENT_TIMESTAMP", "transaction_timestamp()"})
                if expression not in allowed:
                    errors.append(f"DEFAULT: {field} must use {expected.default}")

    for table, key in PRIMARY_KEYS.items():
        if table in present_tables and not any(
            row["table_name"] == table and row["kind"] == "p"
            and set(row["columns"]) == set(key) and not row["deferrable"]
            and row["validated"] and row["index_valid"] and row["index_ready"]
            for row in constraints
        ):
            errors.append(f"PRIMARY KEY: {table} requires an immediate key on ({', '.join(key)})")
    for table, key in UNIQUE_KEYS.items():
        if table in present_tables and not any(
            row["table_name"] == table and row["is_unique"] and row["immediate"]
            and set(row["columns"]) == set(key) for row in indexes
        ):
            errors.append(f"UNIQUE: {table} requires an immediate key on ({', '.join(key)}) for ON CONFLICT")
    for table, key, referred, referred_key in FOREIGN_KEYS:
        if table in present_tables and not any(
            row["table_name"] == table and row["kind"] == "f" and row["validated"]
            and not row["deferrable"] and tuple(row["columns"]) == key
            and row["referred_schema"] == schema_name and row["referred_table"] == referred
            and tuple(row["referred_columns"]) == referred_key and row["on_delete"] == "c"
            for row in constraints
        ):
            errors.append(f"FOREIGN KEY: {table} ({', '.join(key)}) must reference {referred} ({', '.join(referred_key)}) with validated ON DELETE CASCADE")
    for table, checks in CHECKS.items():
        if table not in present_tables:
            continue
        for label, expected in checks.items():
            if not any(
                row["table_name"] == table and row["kind"] == "c" and row["validated"]
                and _expression(row["expression"]) == _expression(expected) for row in constraints
            ):
                errors.append(f"CHECK: {table}.{label} requires the validated runtime definition")
    for table, required in INDEXES.items():
        if table not in present_tables:
            continue
        for key in required:
            if not any(
                row["table_name"] == table and tuple(row["columns"][:len(key)]) == key
                for row in indexes
            ):
                errors.append(f"INDEX: {table} needs a valid nonpartial btree starting with ({', '.join(key)})")

    # Schemas predating the runner can pass structurally without being adopted.
    # If a ledger exists, it must agree with all active migration files.
    if "schema_migrations" in relations:
        try:
            for migration, state in await migration_status(connection, schema_name=schema_name):
                if state == "pending":
                    errors.append(f"MIGRATION HISTORY: pending required migration {migration.filename}")
        except MigrationError as error:
            errors.append(f"MIGRATION HISTORY: {error}")
    return errors


async def verify(database_url: str, *, schema_name: str = "public") -> bool:
    engine = create_async_engine(database_url, connect_args={"timeout": 10})
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            await connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            errors = await schema_errors(connection, schema_name=schema_name)
    finally:
        await engine.dispose()

    if errors:
        print("MIGRATION VERIFICATION FAILED:")
        for error in errors:
            print(f"  x {error}")
        return False
    print(f"Runtime schema verified: {schema_name}.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default="public", help="Existing target schema (default: public)")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    try:
        url = make_url(database_url or "")
        valid = url.drivername == "postgresql+asyncpg" and bool(url.host) and bool(url.database)
    except (ArgumentError, ValueError, TypeError):
        valid = False
    if not valid:
        print("Set DATABASE_URL explicitly to a postgresql+asyncpg URL with a host and database.", file=sys.stderr)
        return 2
    try:
        return 0 if asyncio.run(verify(database_url, schema_name=args.schema)) else 1
    except Exception:
        # SQLAlchemy/driver messages can contain passwords or database URLs.
        print("Migration verification could not complete. Check database access and catalog permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
