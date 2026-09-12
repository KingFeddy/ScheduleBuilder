"""Opt-in database access for tests; never use application database settings."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

from sqlalchemy import make_url, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine


DATABASE_MARKER = "schedule-builder disposable test database v1"
UNCONFIGURED_DATABASE_URL = "postgresql+asyncpg://127.0.0.1:1/disabled_test_database"


class UnsafeTestDatabase(ValueError):
    """The requested database has not been verified as disposable."""


@dataclass(frozen=True)
class DatabaseTestConfig:
    url: str = field(repr=False)


def load_test_database_config(environment: Mapping[str, str]) -> DatabaseTestConfig | None:
    if "TEST_DATABASE_URL" not in environment:
        return None
    if environment.get("APP_ENV") != "test":
        raise UnsafeTestDatabase("Database tests require explicit APP_ENV=test.")

    raw_url = environment["TEST_DATABASE_URL"]
    try:
        url = make_url(raw_url)
        valid = (
            url.drivername == "postgresql+asyncpg"
            and url.host in {"127.0.0.1", "localhost", "::1"}
            and url.port is not None
            and 0 < url.port <= 65535
            and url.database == "njit_test"
            and url.username == "njit_test"
            and bool(url.password)
            and not url.query
        )
    except (ArgumentError, ValueError, TypeError):
        # SQLAlchemy URL errors can include the original credential-bearing input.
        valid = False
    if not valid:
        raise UnsafeTestDatabase(
            "TEST_DATABASE_URL must use postgresql+asyncpg, a loopback host, an explicit "
            "port, the njit_test database and role, a password, and no query parameters. "
            "Use the disposable database documented in README.md."
        ) from None
    return DatabaseTestConfig(raw_url)


def configure_test_environment(
    environment: MutableMapping[str, str], config: DatabaseTestConfig | None,
) -> None:
    # Set these before collection imports src.config or main; no external services
    # or application .env values should be activated by importing a test module.
    environment.update({
        "APP_ENV": "test",
        "DATABASE_URL": config.url if config else UNCONFIGURED_DATABASE_URL,
        "SUPABASE_URL": "http://localhost",
        "SUPABASE_ANON_KEY": "test",
        "CORS_ORIGINS": "http://localhost:3000",
        "CURRENT_TERM": "202690",
        "SENTRY_DSN": "",
        "LOG_LEVEL": "INFO",
    })


async def verify_test_database(config: DatabaseTestConfig) -> None:
    """Attest the server's identity using only reads, before any test fixtures run."""
    engine = create_async_engine(config.url, connect_args={"timeout": 5})
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(text("""
                SELECT current_database() AS database_name,
                       current_user AS role_name,
                       shobj_description(d.oid, 'pg_database') AS marker,
                       r.rolsuper AS is_superuser
                FROM pg_database AS d
                JOIN pg_roles AS r ON r.rolname = current_user
                WHERE d.datname = current_database()
            """))
            identity = result.mappings().one()
            if (
                identity["database_name"] != "njit_test"
                or identity["role_name"] != "njit_test"
                or identity["marker"] != DATABASE_MARKER
                or identity["is_superuser"] is not False
            ):
                raise UnsafeTestDatabase(
                    "Refusing database tests: this server is not the marked disposable "
                    "test database with the restricted njit_test role."
                )
    except UnsafeTestDatabase:
        raise
    except Exception:
        raise UnsafeTestDatabase(
            "Could not verify the disposable test database. Start the test Compose "
            "service and check TEST_DATABASE_URL; no fixtures have been run."
        ) from None
    finally:
        await engine.dispose()
