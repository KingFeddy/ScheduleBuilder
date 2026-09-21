"""Generation must revalidate client data before any planning or catalog work."""
import base64
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.schemas.plan import ParsedDegree
from src.services.plan import GeneratedPlan, validate_parsed_degree
from tests.deployment.test_api_contracts import api  # noqa: F401


def degree(**changes):
    return {
        "majors": ["Synthetic program"], "credits_completed": 114,
        "credits_required": 120, "credits_remaining": 6,
        "still_needed": [{"requirement": "Elective", "options": ["CS435"]}],
        **changes,
    }


@pytest.fixture
def generation(api, monkeypatch):
    from src.routers import plan

    planner = AsyncMock(return_value=GeneratedPlan([], "Synthetic term", []))
    monkeypatch.setattr(plan, "generate_plan", planner)
    return api[0], planner


def assert_invalid(generation, payload, location):
    client, planner = generation
    response = client.post("/api/plan/generate", json=payload)
    assert response.status_code == 422, response.text
    details = response.json()["detail"]
    assert isinstance(details, list), details
    # A single invalid field should not also expose internal ID-generation errors.
    assert len(details) == 1, details
    assert details[0]["loc"] == ["body", *location] and details[0]["msg"], details
    planner.assert_not_awaited()


@pytest.mark.parametrize("data,field", [
    ({}, "majors"),
    (degree(still_needed=[]), "still_needed"),
    (degree(credits_remaining=30), "credits")
])
def test_business_validation_prevents_invalid_generation(generation, data, field):
    assert_invalid(generation, {"parsed_degree": data, "preferences": {}}, ["parsed_degree", field])


@pytest.mark.parametrize("field,value,suffix", [
    ("majors", ["  "], [0]),
    ("credits_completed", True, []),
    ("credits_remaining", -1, []),
    ("still_needed", [{"requirement": " ", "options": []}], [0, "requirement"])
])
def test_malformed_degree_fields_have_structured_errors(generation, field, value, suffix):
    assert_invalid(generation, {"parsed_degree": degree(**{field: value}), "preferences": {}},
                   ["parsed_degree", field, *suffix])


@pytest.mark.parametrize("preferences,location", [
    (None, []),
    ({'courses': ['CS4XX']}, ['courses', 0]),
    ({'courses': ['CS435', ' cs 435 ']}, ['courses']),
    ({'credits_per_semester': 2}, ['credits_per_semester']),
    ({'credits_per_semester': 25}, ['credits_per_semester']),
    ({'credits_per_semester': True}, ['credits_per_semester']),
    ({'electives': []}, ['electives']),
])
def test_invalid_preferences_never_reach_planner(generation, preferences, location):
    assert_invalid(generation, {"parsed_degree": degree(), "preferences": preferences},
                   ["preferences", *location])


@pytest.mark.parametrize("payload,location", [
    ({"preferences": {}}, ["parsed_degree"]),
    ({"parsed_degree": degree(), "preferences": {}, "preferenses": {}}, ["preferenses"])
])
def test_request_envelope_is_typed(generation, payload, location):
    assert_invalid(generation, payload, location)


@pytest.mark.parametrize("number", ["NaN"])
def test_invalid_nonfinite_numbers_return_422_without_echoing_input(generation, number):
    client, planner = generation
    payload = json.dumps({"parsed_degree": degree(), "preferences": {"credits_per_semester": "INVALID_NUMBER"}})
    response = client.post("/api/plan/generate", content=payload.replace('"INVALID_NUMBER"', number),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "preferences", "credits_per_semester"]
    assert "input" not in error and "ctx" not in error
    planner.assert_not_awaited()


@pytest.mark.parametrize("target", [3])
def test_planner_receives_validated_models_and_normalized_preferences(generation, target):
    from src.schemas.plan import ParsedDegreeValidated, PlanPreferences

    client, planner = generation
    data = degree(completed_courses=["cs 100", "CHEM5", "PHYS3XX"],
                  in_progress_courses=["cs 113", "BAD"],
                  still_needed=[{"requirement_id": "req-preserved", "requirement": "Choice",
                                 "options": ["cs 435"], "remaining_quantity": 2, "quantity_unit": "classes"}])
    payload = {"parsed_degree": data, "preferences": {"courses": [" cs 435 "], "credits_per_semester": target}}
    before = deepcopy(payload)
    assert client.post("/api/plan/generate", json=payload).status_code == 200
    validated, preferences, _ = planner.await_args.args
    assert isinstance(validated, ParsedDegreeValidated)
    assert isinstance(preferences, PlanPreferences)
    assert validated.model_dump() == validate_parsed_degree(ParsedDegree.model_validate(data)).model_dump()
    assert validated.completed_courses == ["CS100"]
    assert validated.in_progress_courses == ["CS113"]
    assert validated.still_needed[0].requirement_id == "req-preserved"
    assert validated.still_needed[0].remaining_quantity == 2
    assert preferences.courses == ["CS435"]
    assert preferences.credits_per_semester == target
    assert payload == before


def test_omitted_preference_fields_have_typed_defaults(generation):
    client, planner = generation
    assert client.post("/api/plan/generate", json={"parsed_degree": degree(), "preferences": {}}).status_code == 200
    preferences = planner.await_args.args[1]
    assert preferences.courses == []
    assert preferences.credits_per_semester == 15


def test_unknown_metadata_with_a_requirement_is_not_rejected(generation):
    client, planner = generation
    data = {"majors": ["Synthetic"], "still_needed": [{"requirement": "Unresolved elective", "options": []}]}
    assert client.post("/api/plan/generate", json={"parsed_degree": data, "preferences": {}}).status_code == 200
    validated = planner.await_args.args[0]
    assert validated.credits_remaining is None
    assert validated.still_needed[0].quantity_status == "unresolved"


def test_completed_degree_works_through_real_generation(api):
    client, _ = api
    data = degree(credits_completed=120, credits_remaining=0, still_needed=[])
    response = client.post("/api/plan/generate", json={"parsed_degree": data, "preferences": {}})
    assert response.status_code == 200
    assert response.json()["semesters"] == []
    assert "Congratulations" in " ".join(response.json()["warnings"])


@pytest.mark.parametrize("data", [{"majors": ["Synthetic"]}, degree(still_needed=[]), degree(credits_remaining=30),])
def test_parse_and_generate_apply_the_same_business_rejections(api, monkeypatch, data):
    from src.routers import plan

    client, _ = api
    monkeypatch.setattr(plan, "parse_degree_works_regex", lambda _: ParsedDegree.model_validate(data))
    parsed = client.post("/api/plan/parse", json={
        "pdf_base64": base64.b64encode(b"%PDF-" + b"synthetic" * 800).decode(), "client_pdf_hash": "",
    })
    generated = client.post("/api/plan/generate", json={"parsed_degree": data, "preferences": {}})
    assert parsed.status_code == generated.status_code == 422


def test_generation_schema_exposes_degree_and_preferences(api):
    client, _ = api
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    props = schemas["GenerateRequest"]["properties"]
    assert props["parsed_degree"]["$ref"].endswith("/ParsedDegree")
    assert props["preferences"]["$ref"].endswith("/PlanPreferences")
    assert schemas["PlanPreferences"]["properties"]["credits_per_semester"]["minimum"] == 3
    assert schemas["PlanPreferences"]["properties"]["credits_per_semester"]["maximum"] == 24


def test_real_parser_history_survives_parse_response_and_generation(generation, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from src.services import dw_parser

    text = "\n".join([
        "Student name Synthetic Test Student", "Major Computer Science",
        "Credits required: 120 Credits applied: 114",
        "Still needed: 1 Class in CS 435", "Still needed: 1 Class in HUM 101",
        "CS 100 Synthetic Intro F 3 2024 Fall",
        "CS 100 Synthetic Intro B+ 3 2025 Spring",
        "CS 113 Synthetic Transfer T 3 2025 Fall",
        "CS 114 Synthetic Course W 3 2025 Fall",
        "CS 115 Synthetic Course I 3 2025 Fall",
        "CS 116 Synthetic Course IP (3) 2026 Fall",
        "CS 117 Synthetic Course D 3 2025 Fall",
        "Synthetic padding for PDF extraction length. " * 8,
    ])
    page = SimpleNamespace(extract_text=lambda **_: text)
    monkeypatch.setattr(dw_parser.pdfplumber, "open", lambda _: nullcontext(SimpleNamespace(pages=[page])))
    client, planner = generation
    response = client.post("/api/plan/parse", json={
        "pdf_base64": base64.b64encode(b"%PDF-" + b"synthetic" * 800).decode(), "client_pdf_hash": "",
    })
    assert response.status_code == 200, response.text
    parsed = response.json()["parsed"]
    assert parsed["completed_courses"] == ["CS100", "CS113", "CS117"]
    assert parsed["in_progress_courses"] == ["CS116"]
    history = parsed["course_attempts"]
    assert len(history) == 7
    assert history[1]["grade"] == "B+" and history[1]["term"] == "2025 Spring"
    assert history[2]["grade"] == "T" and history[2]["status"] == "transfer"
    assert history[6]["grade"] == "D"  # Do not silently upgrade this to minimum C.
    assert history[0]["source"]["text"] == "CS 100 Synthetic Intro F 3 2024 Fall"
    # Summary fields and computed flags in browser JSON cannot overrule grades.
    parsed["completed_courses"] += ["CS114", "CS115", "CS116"]
    parsed["course_attempts"][3].update(status="passed", earns_credit=True)
    assert client.post("/api/plan/generate", json={"parsed_degree": parsed, "preferences": {}}).status_code == 200
    validated = planner.await_args.args[0]
    assert validated.completed_courses == ["CS100", "CS113", "CS117"]
    assert validated.course_attempts[3].earns_credit is False
    assert validated.course_attempts[1].model_dump() == history[1]


@pytest.mark.parametrize("value,field", [
    ("3", "credits"),
    (-1, "credits")
])
def test_generation_rejects_malformed_attempts(generation, value, field):
    attempt = {"course_code": "CS100", "grade": "A", "credits": 3, field: value}
    assert_invalid(generation, {"parsed_degree": degree(course_attempts=[attempt]), "preferences": {}},
                   ["parsed_degree", "course_attempts", 0, field])
