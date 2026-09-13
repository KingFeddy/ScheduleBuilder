"""Unknown and variable course metadata must remain explicit at the API boundary."""
from unittest.mock import AsyncMock

import pytest

from tests.deployment.test_api_contracts import api


@pytest.mark.parametrize("credits,source,status,minimum,maximum", [
    (None, None, "missing", None, None),
    (3, None, "unverified", None, None),
    (1, {"value": {"kind": "fixed", "minimum": 1, "maximum": 1}}, "fixed", 1, 1),
    (None, {"value": {"kind": "range", "minimum": 1, "maximum": 4}}, "variable", 1, 4),
])
def test_catalog_and_detail_expose_credit_certainty(api, monkeypatch, credits, source, status, minimum, maximum):
    from src.routers import courses
    client, result = api
    row = {"course_code": "CS999", "title": None, "credits": credits, "prerequisites": [],
           "credits_source": source, "title_source": None, "metadata_latest_attempt": None}
    result.mappings.return_value.all.return_value = [row]
    result.mappings.return_value.first.return_value = row
    monkeypatch.setattr(courses, "load_sections_with_meetings", AsyncMock(return_value={}))
    for path in ("/api/courses", "/api/courses/CS999"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()[0] if path == "/api/courses" else response.json()
        assert body["credits"] == credits
        assert body["credits_status"] == status
        assert (body["credits_min"], body["credits_max"]) == (minimum, maximum)
        assert body["title_status"] == "missing"
        assert body["metadata_warnings"] == []


def test_retained_metadata_reports_latest_refresh_problems(api):
    client, result = api
    result.mappings.return_value.all.return_value = [{
        "course_code": "CS999", "title": "Known", "credits": 4,
        "title_source": {"value": "Known"},
        "credits_source": {"value": {"kind": "fixed", "minimum": 4, "maximum": 4}},
        "metadata_latest_attempt": {"credits_error": "Conflicting section credits.", "title_error": None},
    }]
    response = client.get("/api/courses")
    assert response.status_code == 200
    body = response.json()[0]
    assert body["credits"] == 4 and body["credits_status"] == "fixed"
    assert body["title_status"] == "verified"
    assert "Conflicting section credits." in body["metadata_warnings"][0]
    assert "credits_source" not in body


def test_ger_titles_report_legacy_uncertainty(api):
    client, result = api
    result.mappings.return_value.all.return_value = [{"prefix": "HUM", "course_code": "HUM999", "title": "Legacy title", "title_source": None}]
    response = client.get("/api/plan/ger-courses")
    assert response.status_code == 200
    assert response.json()["groups"][0]["courses"][0]["title_status"] == "unverified"
