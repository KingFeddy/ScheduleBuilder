"""Own a unique PostgreSQL schema for a test, including its committed writes."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema


SCHEMA_SQL = Path(__file__).with_name("create_test_schema.sql").read_text()


@dataclass(frozen=True)
class IsolatedTestDatabase:
    schema_name: str
    session_factory: async_sessionmaker[AsyncSession]
    banner_lock_id: int
    rmp_lock_id: int


@asynccontextmanager
async def isolated_test_database(database_url: str) -> AsyncIterator[IsolatedTestDatabase]:
    """Use only a URL attested by the root test_database_url fixture.

    Schema isolation preserves real commits and independent connections. A
    rollback-only fixture would change the scraper transactions under test.
    """
    identifier = uuid4()
    schema_name = f"test_{identifier.hex}"
    # PostgreSQL advisory locks are database-wide. Reserve a random negative
    # bigint pair for this test; production scraper IDs are positive.
    banner_lock_id = -((identifier.int % (2**62)) * 2 + 1)
    control_engine = create_async_engine(database_url)
    test_engine = None
    owns_schema = False
    try:
        async with control_engine.begin() as connection:
            # No IF NOT EXISTS: a collision must fail without adopting another
            # test's schema. DDL and all schema setup roll back together.
            await connection.execute(CreateSchema(schema_name))
            await connection.execute(
                text("SELECT set_config('search_path', :schema, true)"),
                {"schema": schema_name},
            )
            # asyncpg's simple-query protocol accepts the complete SQL script;
            # SQLAlchemy's prepared execution only accepts one statement.
            raw_connection = await connection.get_raw_connection()
            await raw_connection.driver_connection.execute(SCHEMA_SQL)
        owns_schema = True

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        yield IsolatedTestDatabase(
            schema_name=schema_name,
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            banner_lock_id=banner_lock_id,
            rmp_lock_id=banner_lock_id - 1,
        )
    finally:
        try:
            if test_engine is not None:
                await test_engine.dispose()
        finally:
            try:
                if owns_schema:
                    async with control_engine.begin() as connection:
                        await connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
            finally:
                await control_engine.dispose()
