"""Synthetic DegreeWorks option grammar and downstream preservation."""
import asyncio
import pytest

from src.schemas.plan import ParsedDegree, PlanPreferences
from src.services.dw_parser import _extract_course_codes, _extract_still_needed
from src.services.plan import generate_plan, matches_wildcard, validate_parsed_degree
from tests.plan.test_planner import _make_mock_session


@pytest.mark.parametrize("text,expected", [
    ("CS 490 or 4@", ["CS490", "CS4XX"]),
    ("PHYS 3@ or 4@ or CS 490 or 4@", ["PHYS3XX", "PHYS4XX", "CS490", "CS4XX"]),
    ("COM 303 or\n310 or LIT 320 or 321", ["COM303", "COM310", "LIT320", "LIT321"]),
    ("@ @", ["@"]),
    ("CS @ or 490", ["CSXXX", "CS490"])
])
def test_mixed_options_keep_source_order_and_department_context(text, expected):
    assert _extract_course_codes(text) == expected


@pytest.mark.parametrize("text", [
    "advisor-approved work",
    "@ 3@",
    "CS490 or advisor-approved work",
    "@ @ @"
])
def test_unknown_or_constrained_choices_are_not_silently_partially_resolved(text):
    assert _extract_course_codes(text) == []


@pytest.mark.parametrize("expression", [
    "CS490\nAND 491",
    "CS490\nAND 4@"
])
def test_operator_words_cannot_become_course_subjects(expression):
    assert _extract_course_codes(expression) == []
    [item] = _extract_still_needed(f"Synthetic sequence\nStill needed: 2 Classes in {expression}")
    assert item.options == []
    assert item.remaining_quantity == 2 and item.quantity_unit == "classes"
    assert expression in item.source.text


def test_wrapped_conjunction_remains_unresolved_in_generated_plan():
    [item] = _extract_still_needed("Synthetic sequence\nStill needed: 2 Classes in CS490\nAND 491")
    degree = validate_parsed_degree(ParsedDegree(majors=["Synthetic"], credits_remaining=6, still_needed=[item]))
    generated = asyncio.run(generate_plan(degree, PlanPreferences(), _make_mock_session()))
    slots = [c for s in generated.semesters for c in s.courses if c.requirement is not None]
    assert slots and all(slot.course_code == "TBD" for slot in slots)
    assert all(slot.requirement == item for slot in slots)
    assert any("Course options could not be read" in warning for warning in generated.warnings)
    assert not any("AND" in warning for warning in generated.warnings)


def test_requirement_options_stop_before_neighboring_headings_and_grade_rows():
    text = (
        "Synthetic elective\nStill needed: 1 Class in CS 490 or\n4@ or PHYS 310\n"
        "CS 280 Synthetic completed course A 3 2025 Fall\n"
        "Another requirement\nStill needed: 6 Credits in @ @\n"
        "Next heading\nCS 331 Synthetic failed course F 3 2025 Fall\n"
    )
    first, second = _extract_still_needed(text)
    assert first.options == ["CS490", "CS4XX", "PHYS310"]
    assert second.options == ["@"]
    assert second.remaining_quantity == 6 and second.quantity_unit == "credits"
    assert first.source.text.startswith("Still needed: 1 Class")
    assert "CS 280" in first.source.text  # Original captured source is not rewritten.


@pytest.mark.parametrize("qualification", [
    "with a minimum grade of C",
    "(advisor approval required)"
])
def test_wrapped_unknown_qualifier_does_not_become_unrestricted_options(qualification):
    [item] = _extract_still_needed(f"Synthetic elective\nStill needed: 3 Credits in CS 4@\n{qualification}")
    assert item.options == []
    assert qualification in item.source.text


def test_unknown_requirement_remains_visible_in_actual_plan_with_its_source():
    [item] = _extract_still_needed("Advisor elective\nStill needed: 6 Credits in CS490 or advisor-approved work")
    assert item.options == [] and item.remaining_quantity == 6
    degree = validate_parsed_degree(ParsedDegree(majors=["Synthetic"], credits_remaining=6, still_needed=[item]))
    generated = asyncio.run(generate_plan(degree, PlanPreferences(), _make_mock_session()))
    [slot] = [c for s in generated.semesters for c in s.courses if c.requirement is not None]
    assert slot.course_code == "TBD"
    assert "Course options could not be read" in slot.reason
    assert slot.requirement == item
    assert "advisor-approved work" in slot.requirement.source.text
    assert any("Course options could not be read" in warning for warning in generated.warnings)


@pytest.mark.parametrize("expression,selected", [("@ @", "HUM101")])
def test_parsed_wildcards_match_an_elective_without_creating_an_extra_requirement(expression, selected):
    [item] = _extract_still_needed(f"Synthetic elective\nStill needed: 3 Credits in {expression}")
    degree = validate_parsed_degree(ParsedDegree(majors=["Synthetic"], credits_remaining=3, still_needed=[item]))
    generated = asyncio.run(generate_plan(degree, PlanPreferences(courses=[selected]), _make_mock_session()))
    slots = [c for s in generated.semesters for c in s.courses if c.course_code != "FREE"]
    assert len(slots) == 1 and slots[0].course_code == selected
    assert slots[0].requirement == item


def test_level_and_subject_wildcards_keep_three_digit_contract():
    assert matches_wildcard("PHYS310", _extract_course_codes("PHYS 3@")[0])
    assert not matches_wildcard("PHYS410", _extract_course_codes("PHYS 3@")[0])
    assert matches_wildcard("CS490", _extract_course_codes("CS @")[0])
    assert not matches_wildcard("PHYS490", _extract_course_codes("CS @")[0])


def test_source_order_controls_fallback_among_explicit_options():
    from src.services.plan import select_best_option
    [item] = _extract_still_needed("Choice\nStill needed: 1 Class in CS490 or 4@ or 435")
    session = _make_mock_session()
    chosen, count = asyncio.run(select_best_option(item, set(), set(), [], "202690", session))
    assert (chosen, count) == ("CS490", 2)


def test_mixed_unknown_and_universal_requirements_survive_real_parse_http_round_trip(api, monkeypatch):
    import base64
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.routers import plan
    from src.services import dw_parser
    from src.services.plan import GeneratedPlan

    source = "\n".join([
        "Student name Synthetic Test Student", "Major Computer Science",
        "Credits required: 120 Credits applied: 108",
        "Mixed elective", "Still needed: 1 Class in CS 490 or 4@ or PHYS 310",
        "Universal elective", "Still needed: 6 Credits in @ @",
        "Advisor elective", "Still needed: 3 Credits in CS490 or advisor-approved work",
        "Synthetic footer. " * 30,
    ])
    page = SimpleNamespace(extract_text=lambda **_: source)
    monkeypatch.setattr(dw_parser.pdfplumber, "open", lambda _: nullcontext(SimpleNamespace(pages=[page])))
    planner = AsyncMock(return_value=GeneratedPlan([], "Synthetic term", []))
    monkeypatch.setattr(plan, "generate_plan", planner)
    client, _ = api
    response = client.post("/api/plan/parse", json={
        "pdf_base64": base64.b64encode(b"%PDF-" + b"synthetic" * 800).decode(), "client_pdf_hash": "",
    })
    assert response.status_code == 200, response.text
    parsed = response.json()["parsed"]
    assert [item["options"] for item in parsed["still_needed"]] == [["CS490", "CS4XX", "PHYS310"], ["@"], []]
    assert parsed["still_needed"][1]["remaining_quantity"] == 6
    assert "advisor-approved work" in parsed["still_needed"][2]["source"]["text"]
    assert client.post("/api/plan/generate", json={"parsed_degree": parsed, "preferences": {}}).status_code == 200
    assert [item.model_dump() for item in planner.await_args.args[0].still_needed] == parsed["still_needed"]


# Reuse the existing isolated, synthetic route fixture without a DB lifespan.
from tests.deployment.test_api_contracts import api  # noqa: E402,F401
