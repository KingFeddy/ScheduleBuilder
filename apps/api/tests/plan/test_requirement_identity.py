"""Requirements retain their amount and identity before quantity allocation."""
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.schemas.plan import ParsedDegree, ParsedDegreeValidated, PlanPreferences
from src.services.dw_parser import _extract_still_needed
from src.services import plan
from src.services.course_metadata import course_response


@pytest.mark.parametrize("amount,unit,expected", [
    ("2", "Classes", 2),
    ("6", "Credits", 6),
    ("1.5", "Credits", 1.5)
])
def test_parser_preserves_remaining_amount_and_unit(amount, unit, expected):
    text = f"Synthetic elective\nStill needed: {amount} {unit} in CS 435 or CS 480"
    [item] = _extract_still_needed(text)
    assert item.remaining_quantity == expected
    assert item.quantity_unit == ("classes" if unit.startswith("Class") else "credits")
    assert item.quantity_status == "known"
    assert item.options == ["CS435", "CS480"]
    assert item.source.text == text.split("\n", 1)[1]
    assert item.source.line == 2
    assert item.source.block_index == 1
    assert item.requirement_id


@pytest.mark.parametrize("amount", [
    "?",
    "-2",
    "2.5"
])
def test_unknown_or_invalid_class_amount_remains_unresolved(amount):
    [item] = _extract_still_needed(f"Elective\nStill needed: {amount} Classes in CS 435")
    assert item.remaining_quantity is None
    assert item.quantity_unit == "classes"
    assert item.quantity_status == "unresolved"
    assert item.options == ["CS435"]
    assert f"{amount} Classes" in item.source.text


def test_known_credit_amount_survives_unparsed_options():
    [item] = _extract_still_needed("Open elective\nStill needed: 6 Credits in @ @")
    assert item.remaining_quantity == 6
    assert item.quantity_unit == "credits"
    assert item.source.text.endswith("6 Credits in @ @")


@pytest.mark.parametrize("amount,unit", [
    ("9007199254740993", "Classes"),
    ("2.0000000000000001", "Classes"),
    ("0." + "0" * 340 + "1", "Credits"),
])
def test_unrepresentable_amount_is_unresolved_instead_of_rounded(amount, unit):
    [item] = _extract_still_needed(f"Elective\nStill needed: {amount} {unit} in CS 435")
    assert item.remaining_quantity is None
    assert item.quantity_status == "unresolved"
    assert amount in item.source.text


def test_duplicate_source_requirements_have_stable_distinct_ids():
    block = "Synthetic elective\nStill needed: 2 Classes in CS 435\n"
    first = _extract_still_needed(block + block)
    again = _extract_still_needed(block + block)
    assert [i.requirement_id for i in first] == [i.requirement_id for i in again]
    assert len({i.requirement_id for i in first}) == 2
    assert [i.source.block_index for i in first] == [1, 2]
    assert [i.source.line for i in first] == [2, 4]
    parsed = ParsedDegree(majors=["Synthetic program"], still_needed=first)
    validated = plan.validate_parsed_degree(parsed)
    restored = ParsedDegreeValidated.model_validate_json(validated.model_dump_json())
    assert restored.still_needed == validated.still_needed == first


def test_legacy_requirements_get_repeatable_ids_without_inventing_quantities():
    legacy = {"majors": ["Synthetic"], "still_needed": [
        {"requirement": "Same elective", "options": ["CS435"]},
        {"requirement": "Same elective", "options": ["CS435"]},
        {"requirement": "Other elective", "options": ["CS480"]},
    ]}
    before = deepcopy(legacy)
    first = ParsedDegree.model_validate(legacy)
    second = ParsedDegree.model_validate(legacy)
    assert legacy == before
    assert first.still_needed == second.still_needed
    assert len({i.requirement_id for i in first.still_needed}) == 3
    for item in first.still_needed:
        assert item.remaining_quantity is None
        assert item.quantity_unit == "unknown"
        assert item.quantity_status == "unresolved"
        assert item.source is None
    reordered = first.model_dump()
    reordered["still_needed"].reverse()
    restored = ParsedDegree.model_validate(reordered)
    assert [i.requirement_id for i in restored.still_needed] == [i.requirement_id for i in reversed(first.still_needed)]


def test_explicit_duplicate_ids_are_rejected_instead_of_reassigning_them():
    with pytest.raises(ValidationError, match="Duplicate requirement ID"):
        ParsedDegree(still_needed=[
            {"requirement_id": "req-shared", "requirement": "Major", "options": ["CS435"]},
            {"requirement_id": "req-shared", "requirement": "Minor", "options": ["CS435"]},
        ])


def mock_catalog(monkeypatch):
    async def choose(item, completed, in_progress, *args):
        codes = [c for c in item.options if "X" not in c and c != "@" and c not in completed and c not in in_progress]
        return (codes[0] if codes else None, len(codes))

    async def courses(session, codes):
        return {code: (course_response({"course_code": code, "title": "Synthetic course", "credits": 4 if code == "CS435" else 3}), []) for code in codes}

    monkeypatch.setattr(plan, "select_best_option", choose)
    monkeypatch.setattr(plan, "get_course_data", courses)


def empty_rule_session():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value = []
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [3])
async def test_slots_keep_identity_and_requirement_details_across_regeneration(monkeypatch, target):
    mock_catalog(monkeypatch)
    requirements = _extract_still_needed(
        "Major choice\nStill needed: 2 Classes in CS 435 or CS 480\n"
        "Minor choice\nStill needed: 1 Class in CS 435\n"
        "Open elective\nStill needed: ? Credits in PHYS 3@"
    )
    degree = ParsedDegreeValidated(majors=["Synthetic"], credits_remaining=20, still_needed=requirements)
    before = degree.model_dump()
    first = await plan.generate_plan(degree, PlanPreferences.model_validate({"courses": [], "credits_per_semester": target}), empty_rule_session())
    second = await plan.generate_plan(ParsedDegreeValidated.model_validate_json(degree.model_dump_json()),
                                      PlanPreferences.model_validate({"courses": ["CS480"], "credits_per_semester": 12}), empty_rule_session())
    rows = [c for s in first.semesters for c in s.courses]
    assert all(c.slot_id for c in rows)
    assert len({c.slot_id for c in rows}) == len(rows)
    linked = {r.requirement_id: [c for c in rows if c.requirement and c.requirement.requirement_id == r.requirement_id]
              for r in requirements}
    other = {r.requirement_id: [c for s in second.semesters for c in s.courses
                               if c.requirement and c.requirement.requirement_id == r.requirement_id]
             for r in requirements}
    assert set(linked) == {r.requirement_id for r in requirements}
    for requirement in requirements:
        allocated = linked[requirement.requirement_id]
        assert all(row.requirement == requirement for row in allocated)
        assert {row.slot_id for row in allocated} == {row.slot_id for row in other[requirement.requirement_id]}
    assert {row.course_code for row in linked[requirements[0].requirement_id]} == {"CS480", "TBD"}
    assert {row.course_code for row in other[requirements[0].requirement_id]} == {"CS480", "TBD"}
    assert any("quantity" in w.lower() and "unknown" in w.lower() for w in first.warnings)
    assert degree.model_dump() == before
