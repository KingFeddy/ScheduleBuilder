"""Exercise public JSON contracts through the real ASGI routes using fake data."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def api(monkeypatch):
    from main import app, limiter
    from src.dependencies import get_db

    db = AsyncMock()
    result = MagicMock()
    db.execute.return_value = result
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    # Contract tests cover serialization; rate-limit behavior has separate tests.
    monkeypatch.setattr(limiter, "enabled", False)
    # Without a context manager TestClient does not start the database lifespan.
    with_db_override = TestClient(app)
    yield with_db_override, result
    with_db_override.close()


def test_section_lists_and_solved_sections_keep_distinct_shapes(api, monkeypatch):
    # CONTRACT: the picker receives list sections, while solved calendars also
    # receive term/section_number. Truncation belongs on the search envelope.
    from src.routers import courses, schedule
    from src.scheduler.models import MeetingSlot, SectionSlot

    client, _ = api
    slot = SectionSlot(
        crn="99001", term="202690", course_code="CS280", professor_name=None,
        total_seats=30, open_seats=0, scraped_at=None, section_number=None,
        meetings=[MeetingSlot(crn="99001", term="202690", days=None, start_time=None, end_time=None, location=None)],
    )
    loader = AsyncMock(return_value={"CS280": [slot]})
    monkeypatch.setattr(courses, "load_sections_with_meetings", loader)
    monkeypatch.setattr(schedule, "load_sections_with_meetings", loader)
    listed = client.get("/api/courses/CS280/sections?term=202690")
    assert listed.status_code == 200
    expected = {
        "crn": "99001", "course_code": "CS280", "professor_name": None,
        "total_seats": 30, "open_seats": 0, "scraped_at": None,
        "meetings": [{"days": None, "start_time": None, "end_time": None, "location": None}],
    }
    assert listed.json() == [expected]
    solved = client.post("/api/schedule/solve", json={"course_codes": ["CS280"], "term": "202690"})
    assert solved.status_code == 200
    data = solved.json()
    assert set(data) == {"results", "warnings", "truncated"}
    assert data["truncated"] is False
    assert data["results"] == [{
        "sections": [{**expected, "term": "202690", "section_number": None}],
        "campus_days": 0, "has_async_sections": True,
    }]


def test_empty_truncated_search_keeps_its_flag(api, monkeypatch):
    # CONTRACT: a timed-out search need not contain a schedule to carry its flag.
    from src.routers import schedule
    from src.schemas.schedule import SolveResponse

    client, _ = api
    monkeypatch.setattr(schedule, "load_sections_with_meetings", AsyncMock(return_value={}))
    monkeypatch.setattr(schedule, "solve", lambda **kwargs: SolveResponse(
        results=[], warnings=["Synthetic search limit"], truncated=True,
    ))
    response = client.post("/api/schedule/solve", json={"course_codes": ["CS280"], "term": "202690"})
    assert response.status_code == 200
    assert response.json() == {"results": [], "warnings": ["Synthetic search limit"], "truncated": True}


def test_catalog_and_ger_titles_can_be_null(api):
    # CONTRACT: unknown titles survive serialization instead of inventing data.
    client, result = api
    result.mappings.return_value.all.return_value = [{"course_code": "HUM101", "title": None, "credits": 3}]
    assert client.get("/api/courses").json() == [{
        "course_code": "HUM101", "title": None, "credits": 3,
        "title_status": "missing", "credits_status": "unverified", "credits_min": None,
        "credits_max": None, "credits_options": [], "metadata_warnings": [],
        "catalog_status": "present", "catalog_note": "",
    }]
    result.mappings.return_value.all.return_value = [{"prefix": "HUM", "course_code": "HUM101", "title": None}]
    response = client.get("/api/plan/ger-courses")
    assert response.status_code == 200
    assert response.json()["groups"] == [{"prefix": "HUM", "courses": [{"code": "HUM101", "title": None, "title_status": "missing", "catalog_status": "present", "catalog_note": ""}]}]
    assert "HUM" in response.json()["subjects"]
    assert "HUM" not in response.json()["missing_subjects"]


def test_parse_response_preserves_missing_metadata(api, monkeypatch):
    # CONTRACT: a valid parse with unknown metadata must expose nulls and complete
    # array fields; synthetic upload bytes never reach a real PDF parser.
    from src.routers import plan
    from src.schemas.plan import ParsedDegree

    client, _ = api
    raw = ParsedDegree(majors=["Computer Science"], still_needed=[{
        "requirement_id": "req-synthetic", "requirement": "Unresolved elective", "options": [],
    }])
    monkeypatch.setattr(plan, "parse_degree_works_regex", lambda _: raw)
    response = client.post("/api/plan/parse", json={
        "pdf_base64": base64.b64encode(b"%PDF-" + b"x" * 6000).decode(), "client_pdf_hash": "",
    })
    assert response.status_code == 200
    assert response.json()["parsed"] == {
        "student_name": None, "majors": ["Computer Science"], "minors": [], "catalog_year": None,
        "credits_completed": None, "credits_required": None, "credits_remaining": None,
        "completed_courses": [], "in_progress_courses": [], "course_attempts": None,
        "still_needed": [raw.still_needed[0].model_dump()],
    }


def test_professor_response_preserves_unknown_fields_and_404(api):
    # CONTRACT: a found professor may have null metadata; absence is an HTTP 404,
    # not a successful null JSON payload as the browser mocks previously assumed.
    client, result = api
    result.mappings.return_value.first.side_effect = [
        {"rmp_data": {"rmp_score": None, "rmp_tags": []}, "expires_at": None}, None,
    ]
    response = client.get("/api/professors/Synthetic%20Professor")
    assert response.status_code == 200
    assert response.json() == {
        "rmp_score": None, "rmp_difficulty": None, "rmp_would_take_again": None,
        "rmp_num_ratings": None, "rmp_tags": [], "department": None,
    }
    result.mappings.return_value.first.side_effect = None
    result.mappings.return_value.first.return_value = None
    missing = client.get("/api/professors/Missing%20Professor")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Professor not found in RMP cache."}


def test_generated_plan_serializes_defaults_and_nullable_titles(api, monkeypatch):
    # CONTRACT: the response schema must preserve empty semesters, zero credits,
    # null course titles, badge values, and warning metadata from the planner.
    from src.routers import plan
    from src.services.plan import GeneratedPlan, PlannedCourse, SemesterCard

    client, _ = api
    monkeypatch.setattr(plan, "generate_plan", AsyncMock(return_value=GeneratedPlan(
        semesters=[
            SemesterCard(term="202690", term_label="Fall 2026"),
            SemesterCard(term="202710", term_label="Spring 2027", total_credits=3, courses=[
                PlannedCourse("TBD", None, 3, "TBD", "Synthetic unresolved requirement", "slot-synthetic"),
            ]),
        ], projected_graduation="Spring 2027", warnings=["Synthetic advisory warning"],
    )))
    response = client.post("/api/plan/generate", json={
        "parsed_degree": {"majors": ["Computer Science"], "still_needed": [{"requirement": "Synthetic requirement", "options": []}]}, "preferences": {},
    })
    assert response.status_code == 200
    assert response.json() == {
        "semesters": [
            {"term": "202690", "term_label": "Fall 2026", "courses": [], "total_credits": 0},
            {"term": "202710", "term_label": "Spring 2027", "total_credits": 3, "courses": [{
                "course_code": "TBD", "title": None, "credits": 3, "badge": "TBD",
                "reason": "Synthetic unresolved requirement",
                "slot_id": "slot-synthetic", "requirement": None,
                "credits_estimated": True, "credits_note": "Credit estimate for an unresolved course.", "title_status": "unverified",
                "catalog_status": "unknown", "catalog_note": "Catalog coverage has not been checked. Regenerate the plan to check it.",
            }]},
        ], "projected_graduation": "Spring 2027", "warnings": ["Synthetic advisory warning"],
    }


def test_frontend_endpoints_publish_structured_response_schemas(api):
    # CONTRACT: generated client types cannot catch drift behind an untyped dict.
    client, _ = api
    schema = client.get("/openapi.json").json()
    for path, method in [
        ("/api/scraper/status", "get"), ("/api/plan/generate", "post"),
        ("/api/plan/ger-courses", "get"), ("/api/catalog/coverage", "get"),
    ]:
        response_schema = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in response_schema, f"{method.upper()} {path} must name its response schema"
    assert "$ref" in schema["components"]["schemas"]["ParseResponse"]["properties"]["parsed"]


def test_committed_openapi_matches_the_server(api):
    # CONTRACT: backend changes cannot pass CI with stale frontend schema input.
    client, _ = api
    snapshot = Path(__file__).resolve().parents[2] / "openapi.json"
    assert snapshot.exists(), "Run uv run --no-sync python -m scripts.export_openapi."
    assert json.loads(snapshot.read_text()) == client.get("/openapi.json").json(), (
        "OpenAPI snapshot is stale. Regenerate it and run pnpm --filter web api:generate."
    )


def test_schema_export_ignores_application_environment(api, tmp_path):
    # SAFETY: generating types must not load dotenv credentials, initialize
    # telemetry, start the application lifespan, or connect to a database.
    client, _ = api
    (tmp_path / ".env").write_text("SENTRY_DSN=invalid-dotenv-secret\nDATABASE_URL=invalid-dotenv-database\n")
    environment = {**os.environ, "APP_ENV": "production", "SENTRY_DSN": "invalid-inherited-secret"}
    environment["DATABASE_URL"] = "invalid-inherited-database"
    environment["CATALOG_SUBJECTS"] = "invalid-inherited-subjects"
    environment["GER_SUBJECTS"] = "invalid-inherited-subjects"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [sys.executable, "-m", "scripts.export_openapi", "--stdout"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == client.get("/openapi.json").json()
    assert "invalid-" not in result.stdout + result.stderr
