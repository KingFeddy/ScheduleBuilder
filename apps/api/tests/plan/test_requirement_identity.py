"""Requirements retain their amount and identity before quantity allocation."""
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.schemas.plan import ParsedDegree, ParsedDegreeValidated, PlanPreferences, StillNeededItem
from src.services.dw_parser import _extract_still_needed
from src.services import plan
from src.services.course_metadata import course_response


@pytest.mark.parametrize("amount,unit,expected", [
    ("2", "Classes", 2), ("1", "Class", 1), ("6", "Credits", 6),
    ("1", "Credit", 1), ("1.5", "Credits", 1.5), ("0", "Credits", 0),
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


@pytest.mark.parametrize("amount", ["?", "TBD", "", "-2", "NaN", "2-3", "at least 2", "2.5", "٢"])
def test_unknown_or_invalid_class_amount_remains_unresolved(amount):
    [item] = _extract_still_needed(f"Elective\nStill needed: {amount} Classes in CS 435")
    assert item.remaining_quantity is None
    assert item.quantity_unit == "classes"
    assert item.quantity_status == "unresolved"
    assert item.options == ["CS435"]
    assert f"{amount} Classes" in item.source.text


def test_unsupported_unit_and_options_keep_source_context():
    [item] = _extract_still_needed("Elective\nStill needed: two courses in advisor-approved work")
    assert item.remaining_quantity is None
    assert item.quantity_unit == "unknown"
    assert item.quantity_status == "unresolved"
    assert item.options == []
    assert item.source.text == "Still needed: two courses in advisor-approved work"


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


def test_reused_legacy_model_is_not_mutated_when_assigning_occurrence_ids():
    item = StillNeededItem(requirement="Elective", options=["CS435"])
    original = item.model_dump()
    degree = ParsedDegree(still_needed=[item, item])
    assert len({i.requirement_id for i in degree.still_needed}) == 2
    assert item.model_dump() == original
    assert ParsedDegree(still_needed=[item, item]).still_needed == degree.still_needed


@pytest.mark.parametrize("change", [
    {"remaining_quantity": True}, {"remaining_quantity": "2"},
    {"remaining_quantity": -1}, {"remaining_quantity": float("inf")},
    {"remaining_quantity": float("nan")}, {"remaining_quantity": 1.5},
    {"quantity_unit": "courses"}, {"requirement_id": ""}, {"requirement_id": "bad id"},
])
def test_model_rejects_invalid_quantity_or_identity(change):
    with pytest.raises(ValidationError):
        StillNeededItem(requirement="Elective", options=[], remaining_quantity=change.get("remaining_quantity", 2),
                        **{**{"quantity_unit": "classes"}, **{k: v for k, v in change.items() if k != "remaining_quantity"}})


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
@pytest.mark.parametrize("target", [3, 12, 24])
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


@pytest.mark.asyncio
async def test_extra_electives_have_distinct_stable_non_requirement_slots_without_filler(monkeypatch):
    mock_catalog(monkeypatch)
    degree = ParsedDegreeValidated(majors=["Synthetic"], credits_remaining=12,
        still_needed=_extract_still_needed("Senior project\nStill needed: 1 Class in HSS 404"))
    preferences = {"courses": ["CS400", "CS401"], "credits_per_semester": 12}
    first = await plan.generate_plan(degree, PlanPreferences.model_validate(preferences), empty_rule_session())
    second = await plan.generate_plan(degree, PlanPreferences.model_validate(preferences), empty_rule_session())
    assert asdict(first) == asdict(second)
    rows = [c for s in first.semesters for c in s.courses]
    assert len({c.slot_id for c in rows}) == len(rows)
    assert {c.course_code for c in rows} == {"HSS404", "CS400", "CS401"}
    assert all(c.requirement is None for c in rows if c.course_code in {"CS400", "CS401"})
    assert all(c.requirement is not None for c in rows if c.course_code == "HSS404")
