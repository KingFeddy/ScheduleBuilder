from unittest.mock import AsyncMock

import pytest

from tests.deployment.test_api_contracts import api


@pytest.mark.parametrize("query", [
    "",
    "?term=２０２６90"
])
def test_scraper_status_requires_an_explicit_valid_term(api, query):
    client, result = api
    result.mappings.return_value.first.return_value = None
    assert client.get("/api/scraper/status" + query).status_code == 422


@pytest.mark.parametrize("status", [
    "never_run",
    "completed",
    "failed",
    "skipped_overlap"
])
def test_status_response_separates_attempt_success_and_unknown_data(api, monkeypatch, status):
    import main
    client, result = api
    result.mappings.return_value.first.return_value = None
    stamp = "2026-01-01T12:00:00Z"
    run = dict(status=status, subjects=["CS"], started_at=stamp, finished_at=None,
               sections_upserted=0, sections_failed=None, error_message=None)
    expected = dict(term="202710", status=status, checked_at=stamp,
                    latest_attempt=None if status == "never_run" else run,
                    last_successful_refresh=None, data_as_of=None,
                    section_count=0, sections_missing_timestamps=0)
    loader = AsyncMock(return_value=expected)
    monkeypatch.setattr(main, "load_scraper_status", loader, raising=False)
    response = client.get("/api/scraper/status?term=202710")
    assert response.status_code == 200
    assert response.json() == expected
    assert response.headers.get("cache-control") == "no-store"
    assert loader.await_args.args[-1] == "202710"
