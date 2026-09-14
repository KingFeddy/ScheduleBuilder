from src.config import settings
from tests.deployment.test_api_contracts import api


def test_coverage_endpoint_has_empty_and_outside_subjects(api, monkeypatch):
    client, result = api
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS,HSS")
    monkeypatch.setattr(settings, "GER_SUBJECTS", "HUM")
    result.mappings.return_value.all.return_value = [
        {"subject": "CS", "course_count": 2, "section_count": 3},
        {"subject": "ZZZ", "course_count": 1, "section_count": 0},
    ]
    response = client.get("/api/catalog/coverage?term=202690")
    assert response.status_code == 200
    data = response.json()
    assert data["term"] == "202690" and data["configured_subjects"] == ["CS", "HSS"]
    assert data["elective_subjects"] == ["HUM"]
    assert data["subjects"] == [
        {"subject": "CS", "configured": True, "course_count": 2, "section_count": 3},
        {"subject": "HSS", "configured": True, "course_count": 0, "section_count": 0},
        {"subject": "HUM", "configured": False, "course_count": 0, "section_count": 0},
        {"subject": "ZZZ", "configured": False, "course_count": 1, "section_count": 0},
    ]
    assert client.get("/api/catalog/coverage?term=invalid").status_code == 422


def test_ger_browser_uses_scope_and_reports_missing_subjects(api, monkeypatch):
    client, result = api
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS,HSS")
    monkeypatch.setattr(settings, "GER_SUBJECTS", "HSS,HUM")
    result.mappings.return_value.all.return_value = [{"prefix": "HUM", "course_code": "HUM999", "title": None}]
    data = client.get("/api/plan/ger-courses").json()
    assert data["subjects"] == ["HSS", "HUM"]
    assert data["missing_subjects"] == ["HSS"]
    assert data["unconfigured_subjects"] == ["HUM"]
    assert data["groups"][0]["courses"][0]["catalog_status"] == "subject_not_configured"


def test_catalog_response_keeps_excluded_subject_explicit(api, monkeypatch):
    client, result = api
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    result.mappings.return_value.all.return_value = [{"course_code": "IS999", "title": None, "credits": None}]
    course = client.get("/api/courses").json()[0]
    assert course["catalog_status"] == "subject_not_configured"
    assert "outside" in course["catalog_note"]
