"""Regression checks for the boundary between test writes and application data."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.database import (
    DATABASE_MARKER,
    UNCONFIGURED_DATABASE_URL,
    UnsafeTestDatabase,
    configure_test_environment,
    load_test_database_config,
    verify_test_database,
)


TEST_URL = "postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test"


def test_application_database_is_never_used_as_a_test_default():
    # SAFETY: an inherited deployment URL must never authorize fixture writes.
    environment = {"DATABASE_URL": "postgresql+asyncpg://secret@production/app"}
    assert load_test_database_config(environment) is None
    configure_test_environment(environment, None)
    assert environment["DATABASE_URL"] == UNCONFIGURED_DATABASE_URL
    assert environment["APP_ENV"] == "test"


@pytest.mark.parametrize("app_env", [None, "", "development", "production"])
def test_database_access_requires_explicit_test_mode(app_env):
    # SAFETY: a test URL alone must not enable writes from a regular app process.
    environment = {"TEST_DATABASE_URL": TEST_URL}
    if app_env is not None:
        environment["APP_ENV"] = app_env
    with pytest.raises(UnsafeTestDatabase, match="APP_ENV=test"):
        load_test_database_config(environment)


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://njit_test:private-password@production.example:5432/njit_test",
    "postgresql+asyncpg://njit_test:private-password@127.0.0.1:55432/production",
    "postgresql+asyncpg://postgres:private-password@127.0.0.1:55432/njit_test",
    "postgresql+asyncpg://njit_test:private-password@127.0.0.1/njit_test",
    "postgresql+asyncpg://njit_test:private-password@127.0.0.1:55432/njit_test?host=production.example",
    "postgresql+asyncpg://njit_test:private-password@127.0.0.1:55432/njit_test?port=5432",
    "postgresql+asyncpg://njit_test:private-password@127.0.0.1:55432/njit_test?server_settings=unsafe",
    "postgresql://njit_test:private-password@127.0.0.1:55432/njit_test",
    "postgresql+asyncpg://njit_test:private-password@/njit_test",
    "not-a-url-private-password",
    "",
])
def test_unsafe_targets_and_connection_overrides_are_rejected(url):
    # SAFETY: names, hosts, driver overrides, and query arguments can redirect writes.
    with pytest.raises(UnsafeTestDatabase) as error:
        load_test_database_config({"APP_ENV": "test", "TEST_DATABASE_URL": url})
    assert "private-password" not in str(error.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_explicit_local_test_database_is_accepted(host):
    # CONTRACT: both the documented local command and localhost-based CI are allowed.
    url = TEST_URL.replace("127.0.0.1", host)
    config = load_test_database_config({"APP_ENV": "test", "TEST_DATABASE_URL": url})
    assert config is not None
    assert config.url == url


def test_test_environment_disables_inherited_external_services():
    # SAFETY: collecting tests must not initialize production telemetry or services.
    environment = {
        "APP_ENV": "test", "TEST_DATABASE_URL": TEST_URL,
        "DATABASE_URL": "production", "SENTRY_DSN": "production",
        "SUPABASE_URL": "production", "SUPABASE_ANON_KEY": "production",
        "CORS_ORIGINS": "production", "CURRENT_TERM": "209990",
        "CATALOG_SUBJECTS": "invalid-production", "GER_SUBJECTS": "invalid-production",
    }
    config = load_test_database_config(environment)
    configure_test_environment(environment, config)
    assert environment["DATABASE_URL"] == TEST_URL
    assert environment["SENTRY_DSN"] == ""
    assert environment["SUPABASE_URL"] == "http://localhost"
    assert environment["SUPABASE_ANON_KEY"] == "test"
    assert environment["CURRENT_TERM"] == "202690"
    from src.catalog import DEFAULT_CATALOG_SUBJECTS, DEFAULT_GER_SUBJECTS
    assert environment["CATALOG_SUBJECTS"] == DEFAULT_CATALOG_SUBJECTS
    assert environment["GER_SUBJECTS"] == DEFAULT_GER_SUBJECTS


def test_test_settings_do_not_read_an_application_dotenv(tmp_path, monkeypatch):
    # SAFETY: a project .env must not supply secrets even when a test setting is absent.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SENTRY_DSN=dotenv-secret\n")
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from src.config import Settings

    settings = Settings(
        DATABASE_URL=UNCONFIGURED_DATABASE_URL,
        SUPABASE_URL="http://localhost", SUPABASE_ANON_KEY="test", APP_ENV="test",
    )
    assert settings.SENTRY_DSN == ""


def mock_database(monkeypatch, **identity_overrides):
    identity = {
        "database_name": "njit_test", "role_name": "njit_test",
        "marker": DATABASE_MARKER, "is_superuser": False,
    }
    identity.update(identity_overrides)
    result = MagicMock()
    result.mappings.return_value.one.return_value = identity
    connection = AsyncMock()
    connection.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context
    engine.dispose = AsyncMock()
    monkeypatch.setattr("tests.database.create_async_engine", lambda *args, **kwargs: engine)
    return engine, connection


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"marker": None}, {"marker": "some-other-test-database"},
    {"database_name": "production"}, {"role_name": "postgres"},
    {"is_superuser": True},
])
async def test_database_identity_is_checked_read_only_before_fixtures(monkeypatch, identity):
    # SAFETY: a localhost tunnel or lookalike database is not proof of disposability.
    engine, connection = mock_database(monkeypatch, **identity)
    config = load_test_database_config({"APP_ENV": "test", "TEST_DATABASE_URL": TEST_URL})
    with pytest.raises(UnsafeTestDatabase, match="disposable"):
        await verify_test_database(config)
    statements = [str(call.args[0]).strip() for call in connection.execute.await_args_list]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(statement.upper().startswith("SELECT") for statement in statements[1:])
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_marked_database_passes_and_verification_connection_is_closed(monkeypatch):
    # CONTRACT: successful verification must release its connection before test loops start.
    engine, _ = mock_database(monkeypatch)
    config = load_test_database_config({"APP_ENV": "test", "TEST_DATABASE_URL": TEST_URL})
    await verify_test_database(config)
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_failures_do_not_disclose_credentials(monkeypatch):
    # SAFETY: setup errors must not echo credentials embedded in driver exceptions.
    engine, _ = mock_database(monkeypatch)
    engine.connect.return_value.__aenter__.side_effect = RuntimeError("private-password")
    config = load_test_database_config({"APP_ENV": "test", "TEST_DATABASE_URL": TEST_URL})
    with pytest.raises(UnsafeTestDatabase) as error:
        await verify_test_database(config)
    assert "private-password" not in str(error.value)
    engine.dispose.assert_awaited_once()


def run_pytest_probe(tmp_path, source, **environment_overrides):
    """Load the real safety plugin in a separate pytest process, without a DB."""
    (tmp_path / "test_probe.py").write_text(source)
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = database: database test\n")
    environment = os.environ.copy()
    for name in ("TEST_DATABASE_URL", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        environment.pop(name, None)
    environment.update({
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://secret@production.example/application",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        **environment_overrides,
    })
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider", "-q"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
    )


def test_missing_opt_in_stops_pytest_before_even_autouse_fixtures(tmp_path):
    # SAFETY: checking inside db_session is too late if another fixture writes first.
    result = run_pytest_probe(tmp_path, """
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def write_before_test():
    Path('fixture-ran').write_text('unsafe')

@pytest.fixture
def db_session():
    return None

def test_database_write(db_session):
    pass
""")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "No fixtures have been run" in result.stderr
    assert not (tmp_path / "fixture-ran").exists()


def test_remote_test_url_stops_pytest_before_test_module_import(tmp_path):
    # SAFETY: bad destinations must fail before collection can import application code.
    result = run_pytest_probe(
        tmp_path, "from pathlib import Path\nPath('module-imported').touch()\n",
        APP_ENV="test",
        TEST_DATABASE_URL="postgresql+asyncpg://njit_test:private-password@production.example:5432/njit_test",
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert not (tmp_path / "module-imported").exists()
    assert "private-password" not in result.stdout + result.stderr


def test_pure_tests_use_safe_settings_without_database_credentials(tmp_path):
    # CONTRACT: pure tests stay usable without Docker, .env, or production services.
    (tmp_path / ".env").write_text("SENTRY_DSN=dotenv-secret\n")
    result = run_pytest_probe(tmp_path, """
from src.config import settings

def test_safe_settings():
    assert settings.APP_ENV == 'test'
    assert settings.DATABASE_URL == 'postgresql+asyncpg://127.0.0.1:1/disabled_test_database'
    assert settings.SENTRY_DSN == ''
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_normal_application_startup_still_loads_dotenv(tmp_path):
    # COMPATIBILITY: the test safeguard must preserve normal local app configuration.
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost/example\n"
        "SUPABASE_URL=http://localhost\nSUPABASE_ANON_KEY=example\n"
        "SENTRY_DSN=dotenv-value\n"
    )
    environment = os.environ.copy()
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SENTRY_DSN"):
        environment.pop(name, None)
    environment["APP_ENV"] = "development"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [sys.executable, "-c", (
            "from src.config import settings; "
            "assert settings.DATABASE_URL == 'postgresql+asyncpg://localhost/example'; "
            "assert settings.SENTRY_DSN == 'dotenv-value'"
        )],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
