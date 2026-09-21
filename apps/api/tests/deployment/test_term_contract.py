"""The configured default and collected terms share one public contract."""
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.config import settings
from tests.deployment.test_api_contracts import api
from tests.deployment.test_config_validation import make_settings


@pytest.mark.parametrize("value", [
    "",
    "20269"
])
def test_invalid_configured_default_is_rejected(value):
    with pytest.raises(ValidationError):
        make_settings(CURRENT_TERM=value)


@pytest.mark.parametrize("default,stored,expected", [
    ("202710", ["202690", "202710"], [
        {"code": "202690", "label": "Fall 2026", "has_data": True},
        {"code": "202710", "label": "Spring 2027", "has_data": True},
    ]),
    ("202750", [], [{"code": "202750", "label": "Summer 2027", "has_data": False}])
])
def test_term_discovery_preserves_default_and_exposes_absent_data(api, monkeypatch, default, stored, expected):
    client, result = api
    monkeypatch.setattr(settings, "CURRENT_TERM", default)
    result.scalars.return_value.all.return_value = stored
    response = client.get("/api/terms")
    assert response.status_code == 200
    assert response.json() == {"default_term": default, "terms": expected}
    assert client.get("/api/version").json()["term"] == default


def test_course_detail_uses_default_or_explicit_valid_term(api, monkeypatch):
    from src.routers import courses
    client, result = api
    monkeypatch.setattr(settings, "CURRENT_TERM", "202710")
    result.mappings.return_value.first.return_value = {
        "course_code": "CS999", "title": None, "credits": None, "prerequisites": [],
    }
    loader = AsyncMock(return_value={})
    monkeypatch.setattr(courses, "load_sections_with_meetings", loader)
    assert client.get("/api/courses/CS999").status_code == 200
    assert loader.await_args.args[-1] == "202710"
    assert client.get("/api/courses/CS999?term=202690").status_code == 200
    assert loader.await_args.args[-1] == "202690"
    assert client.get("/api/courses/CS999?term=invalid").status_code == 422
