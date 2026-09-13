"""Configure safe test settings before importing any application modules."""
from __future__ import annotations

import asyncio
import os

import pytest

from tests.database import (
    UnsafeTestDatabase,
    configure_test_environment,
    load_test_database_config,
    verify_test_database,
)


try:
    _database_config = load_test_database_config(os.environ)
except UnsafeTestDatabase as error:
    raise pytest.UsageError(str(error)) from None
configure_test_environment(os.environ, _database_config)


def pytest_sessionstart(session):
    if _database_config is not None:
        try:
            asyncio.run(verify_test_database(_database_config))
        except UnsafeTestDatabase as error:
            raise pytest.UsageError(str(error)) from None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        if {"db_session", "db_session_factory", "test_database_url"}.intersection(item.fixturenames):
            item.add_marker(pytest.mark.database)


def pytest_collection_finish(session):
    # Runs after -m/-k deselection but before any fixture setup, including autouse.
    if _database_config is None and any(
        item.get_closest_marker("database") is not None for item in session.items
    ):
        raise pytest.UsageError(
            "Database tests require APP_ENV=test and an explicit TEST_DATABASE_URL "
            "for the disposable database. See README.md, or select only pure tests "
            "with -m 'not database'. No fixtures have been run."
        )


@pytest.fixture(scope="session")
def test_database_url():
    if _database_config is None:
        pytest.fail("The disposable test database was not configured.")
    return _database_config.url
