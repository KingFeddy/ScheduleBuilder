"""Explicit audit-owned choices regenerate complete plans without a database."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.schemas.plan import ParsedDegree, ParseValidationError, PlanPreferences, StillNeededItem
from src.services import plan
from tests.plan.test_requirement_allocation import catalog  # noqa: F401
from tests.plan.test_prerequisite_checks import condition, stored
from tests.deployment.test_api_contracts import api  # noqa: F401


def requirement(key="choice", options=("CS300", "CS400"), amount=1, unit="classes"):
    return StillNeededItem(requirement_id=key, requirement=f"Synthetic {key}", options=list(options),
                           remaining_quantity=amount, quantity_unit=unit,
                           source={"block_index": 1, "line": 2, "text": "Synthetic original source"})


def session_for(present=(), rules=()):
    def execute(statement, params):
        result = MagicMock()
        if "prerequisites_rules" in str(statement):
            result.mappings.return_value = list(rules)
        elif str(statement).startswith("SELECT course_code FROM courses"):
            result.mappings.return_value = [{"course_code": code} for code in present if code in params["codes"]]
        else:
            result.mappings.return_value = []
        return result
    session = AsyncMock()
    session.execute.side_effect = execute
    return session


async def generate(items, choices=None, *, present=(), rules=(), courses=(), **history):
    degree = plan.validate_parsed_degree(ParsedDegree(majors=["Synthetic"], credits_remaining=6,
                                                    still_needed=items, **history))
    prefs = PlanPreferences(requirement_choices=choices or {}, courses=list(courses),
                            credits_per_semester=6, start_term="202690")
    generated = await plan.generate_plan(degree, prefs, session_for(present, rules))
    return generated, [row for semester in generated.semesters for row in semester.courses]


@pytest.mark.parametrize("choices", [
    {"choice": []},
    {"choice": ["CS3XX"]},
    {"choice": ["CS300", "cs 300"]},
    {"one": ["CS300"], "two": ["CS300"]},
    {"one": [f"CS{code}" for code in range(100, 201)],
     "two": [f"CS{code}" for code in range(201, 301)]}
])
def test_invalid_or_duplicate_choices_are_rejected(choices):
    with pytest.raises(ValidationError):
        PlanPreferences(requirement_choices=choices)


def test_choices_normalize_and_default_to_backwards_compatible_empty_map():
    assert PlanPreferences().requirement_choices == {}
    assert PlanPreferences(requirement_choices={"req:1": [" cs 300 "]}).requirement_choices == {"req:1": ["CS300"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("item,choices,history,message", [
    (requirement(), {"stale": ["CS300"]}, {}, "not in the submitted audit"),
    (requirement(), {"choice": ["HUM300"]}, {}, "does not match"),
    (requirement(amount=0), {"choice": ["CS300"]}, {}, "no remaining quantity"),
    (requirement(), {"choice": ["CS300", "CS400"]}, {}, "remaining class count"),
    (requirement(), {"choice": ["CS300"]}, {"completed_courses": ["CS300"]}, "already completed")
])
async def test_semantic_errors_reject_instead_of_silently_substituting(item, choices, history, message):
    with pytest.raises(ParseValidationError, match=message):
        await generate([item], choices, **history)


@pytest.mark.asyncio
async def test_missing_catalog_course_rejected_even_for_excluded_subject(catalog):
    with pytest.raises(ParseValidationError, match="not found in the collected catalog"):
        await generate([requirement(options=("ZZZ300",))], {"choice": ["ZZZ300"]})
    catalog("ZZZ300")
    _, rows = await generate([requirement(options=("ZZZ300",))], {"choice": ["ZZZ300"]}, present=["ZZZ300"])
    assert [r.course_code for r in rows] == ["ZZZ300"]


@pytest.mark.asyncio
async def test_choice_reloads_metadata_and_recalculates_terms_and_rules(catalog):
    catalog("CS100", 3)
    catalog("CS300", 3)
    catalog("CS400", 4)
    items = [requirement(), requirement("foundation", ("CS100",))]
    original = [item.model_dump() for item in items]
    before, _ = await generate(items)
    after, rows = await generate(items, {"choice": ["CS400"]}, present=["CS400"],
                                 rules=[stored("CS400", prereq=condition("CS100"))])
    assert len(before.semesters) == 1
    assert [s.total_credits for s in after.semesters] == [3, 4]
    assert before.projected_graduation != after.projected_graduation
    assert [r.course_code for r in rows] == ["CS100", "CS400"]
    chosen = rows[-1]
    assert chosen.title == "Synthetic CS400" and chosen.credits == 4 and not chosen.credits_estimated
    assert chosen.requirement.model_dump() == original[0]
    assert [item.model_dump() for item in items] == original
    assert chosen.allocation.allocated_quantity == 1
    assert not any("CS300:" in warning for warning in after.warnings)


@pytest.mark.asyncio
async def test_wildcard_choice_reserved_for_its_owner_before_narrow_requirements(catalog):
    for code in ["CS300", "CS400"]:
        catalog(code)
    narrow = requirement("narrow", ("CS300",))
    broad = requirement("broad", ("CS3XX",))
    _, rows = await generate([narrow, broad], {"broad": ["CS300"]}, present=["CS300"], courses=["CS300"])
    assert [(r.requirement.requirement_id, r.course_code) for r in rows].count(("broad", "CS300")) == 1
    assert any(r.requirement.requirement_id == "narrow" and r.course_code == "TBD" for r in rows)
    assert len([r for r in rows if r.course_code == "CS300"]) == 1


@pytest.mark.asyncio
async def test_multiple_pinned_requirements_keep_all_selected_courses(catalog):
    for code in ["CS300", "CS400", "CS500"]:
        catalog(code)
    items = [requirement("first", ("CS300", "CS400", "CS500"), 2),
             requirement("second", ("CS300", "CS400", "CS500"))]
    _, rows = await generate(items, {"first": ["CS500", "CS400"], "second": ["CS300"]},
                             present=["CS300", "CS400", "CS500"])
    assert {(r.requirement.requirement_id, r.course_code) for r in rows} == {
        ("first", "CS500"), ("first", "CS400"), ("second", "CS300")}
    assert len({r.slot_id for r in rows}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("unit,amount,remainder", [("classes", 2, 1), ("credits", 6, 3)])
async def test_partial_choice_preserves_tbd_remainder_without_readding_old_course(catalog, unit, amount, remainder):
    catalog("CS300")
    catalog("CS400")
    _, rows = await generate([requirement(amount=amount, unit=unit)], {"choice": ["CS400"]}, present=["CS400"])
    assert {r.course_code for r in rows} == {"CS400", "TBD"}
    assert all(r.allocation.unresolved_quantity == remainder for r in rows)


@pytest.mark.asyncio
async def test_uncertain_multi_course_credits_kept_without_certifying_remaining_credits(catalog):
    catalog("CS300", 4, "variable")
    catalog("CS400", 3)
    _, rows = await generate([requirement(amount=6, unit="credits")], {"choice": ["CS300", "CS400"]},
                             present=["CS300", "CS400"])
    assert {r.course_code for r in rows} == {"CS300", "CS400"}
    assert all(r.allocation.unresolved_quantity == 3 and r.allocation.status == "partial" for r in rows)
    assert next(r for r in rows if r.course_code == "CS300").credits_estimated


@pytest.mark.asyncio
async def test_selected_credit_surplus_is_counted_instead_of_silently_discarded(catalog):
    catalog("CS300", 4)
    catalog("CS400", 3)
    _, rows = await generate([requirement(amount=3, unit="credits")], {"choice": ["CS300", "CS400"]},
                             present=["CS300", "CS400"])
    assert {r.course_code for r in rows} == {"CS300", "CS400"}
    assert all(r.allocation.allocated_quantity == 7 for r in rows)


@pytest.mark.asyncio
async def test_single_choice_keeps_unknown_quantity_unresolved(catalog):
    catalog("CS400")
    _, rows = await generate([requirement(amount=None)], {"choice": ["CS400"]}, present=["CS400"])
    assert len(rows) == 1 and rows[0].allocation.status == "unknown"


@pytest.mark.asyncio
async def test_allocation_limit_rejects_instead_of_dropping_explicit_choices(catalog, monkeypatch):
    catalog("CS300")
    catalog("CS400")
    monkeypatch.setattr(plan, "MAX_QUANTITY_ALLOCATION_SLOTS", 1)
    with pytest.raises(ParseValidationError, match="allocation limit"):
        await generate([requirement(amount=2)], {"choice": ["CS300", "CS400"]}, present=["CS300", "CS400"])


@pytest.mark.parametrize("choices,status,expected", [
    ({"choice": ["cs 400"]}, 200, "CS400"),
    ({"stale": ["CS400"]}, 422, "not in the submitted audit"),
    ({"choice": ["HUM300"]}, 422, "does not match"),
    ({"choice": ["CS300", "CS300"]}, 422, "once"),
])
def test_real_http_contract_validates_and_generates(api, catalog, monkeypatch, choices, status, expected):
    from main import app
    from src.dependencies import get_db
    client, _ = api
    db = session_for(["CS300", "CS400"])
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    catalog("CS300", 3)
    catalog("CS400", 4)
    response = client.post("/api/plan/generate", json={
        "parsed_degree": {"majors": ["Synthetic"], "credits_remaining": 6,
                          "still_needed": [requirement().model_dump()]},
        "preferences": {"requirement_choices": choices},
    })
    assert response.status_code == status, response.text
    if status == 200:
        rows = [r for s in response.json()["semesters"] for r in s["courses"]]
        assert len(rows) == 1 and rows[0]["course_code"] == expected and rows[0]["credits"] == 4
        assert rows[0]["requirement"]["requirement_id"] == "choice"
    else:
        assert expected in response.text
        assert '"input"' not in response.text


@pytest.mark.asyncio
async def test_universal_wildcard_accepts_a_collected_choice(catalog):
    catalog("CS300")
    _, rows = await generate([requirement(options=("@",))], {"choice": ["CS300"]}, present=["CS300"])
    assert len(rows) == 1 and rows[0].course_code == "CS300"


@pytest.mark.asyncio
async def test_large_remainder_cannot_consume_later_explicit_selection_budget(catalog, monkeypatch):
    for code in ["CS300", "CS400", "CS500"]:
        catalog(code)
    monkeypatch.setattr(plan, "MAX_QUANTITY_ALLOCATION_SLOTS", 4)
    _, rows = await generate([
        requirement("first", ("CS300",), 100),
        requirement("second", ("CS400", "CS500"), 2),
    ], {"first": ["CS300"], "second": ["CS400", "CS500"]}, present=["CS300", "CS400", "CS500"])
    assert len(rows) == 4
    assert {r.course_code for r in rows} == {"CS300", "CS400", "CS500", "TBD"}
    assert all(r.allocation.unresolved_quantity == 99 for r in rows if r.requirement.requirement_id == "first")
