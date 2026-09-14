"""Quantity allocation against synthetic catalog metadata, without a database."""
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.courses import CourseResponse
from src.schemas.plan import ParsedDegree, PlanPreferences, StillNeededItem
from src.services import plan


@pytest.fixture
def catalog(monkeypatch):
    records = {}

    def add(code, credits=3, status="fixed", prerequisites=()):
        records[code] = (CourseResponse(
            course_code=code, title=f"Synthetic {code}", title_status="verified",
            credits=credits, credits_status=status,
            credits_min=1 if status == "variable" else None,
            credits_max=credits if status == "variable" else None,
            catalog_status="present", catalog_note="",
        ), list(prerequisites))

    async def data(session, codes):
        return {code: records.get(code, (CourseResponse(
            course_code=code, title=None, credits=None, credits_status="missing",
            catalog_status="course_missing", catalog_note="Synthetic missing metadata",
        ), [])) for code in codes}

    monkeypatch.setattr(plan, "get_course_data", data)
    return add


async def allocate(amount, unit, options, electives=(), credit_target=15, **kwargs):
    item = StillNeededItem(requirement_id="req-quantity", requirement="Synthetic elective",
                          remaining_quantity=amount, quantity_unit=unit, options=options)
    degree = plan.validate_parsed_degree(ParsedDegree(
        majors=["Synthetic"], credits_remaining=kwargs.pop("credits_remaining", 12),
        still_needed=[item], **kwargs,
    ))
    result = MagicMock()
    result.mappings.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    generated = await plan.generate_plan(degree, PlanPreferences(courses=list(electives), credits_per_semester=credit_target), session)
    rows = [c for s in generated.semesters for c in s.courses]
    return generated, [r for r in rows if r.requirement is not None], [r for r in rows if r.requirement is None]


@pytest.mark.asyncio
async def test_two_classes_get_distinct_courses_and_stable_occurrence_ids(catalog):
    catalog("CS435", 4)
    catalog("CS480", 2)
    _, rows, extras = await allocate(2, "classes", ["CS435", "CS480"])
    assert {r.course_code for r in rows} == {"CS435", "CS480"}
    assert len(rows) == len({r.slot_id for r in rows}) == 2 and not extras
    assert sum(r.credits for r in rows) == 6
    for row in rows:
        assert row.allocation.status == "allocated"
        assert row.allocation.allocated_quantity == 2
        assert row.allocation.unresolved_quantity == 0
    _, again, _ = await allocate(2, "classes", ["CS435", "CS480"], electives=["CS480"])
    assert {r.slot_id for r in again} == {r.slot_id for r in rows}


@pytest.mark.asyncio
@pytest.mark.parametrize("credits", [(3, 3), (4, 2), (4, 3), (0.1, 0.2)])
async def test_credit_requirements_use_actual_credits_without_splitting_courses(catalog, credits):
    for code, value in zip(["CS435", "CS480"], credits):
        catalog(code, value)
    amount = 0.3 if credits == (0.1, 0.2) else 6
    _, rows, _ = await allocate(amount, "credits", ["CS435", "CS480"])
    assert len(rows) == 2
    assert {r.course_code for r in rows} == {"CS435", "CS480"}
    assert all(r.allocation.unresolved_quantity == 0 for r in rows)
    assert rows[0].allocation.allocated_quantity == pytest.approx(sum(credits))


@pytest.mark.asyncio
async def test_two_requested_electives_can_fill_one_requirement(catalog):
    catalog("CS435", 4)
    catalog("CS480", 2)
    _, rows, extras = await allocate(6, "credits", ["CS4XX"], electives=["CS435", "CS480"])
    assert {r.course_code for r in rows} == {"CS435", "CS480"}
    assert not extras and rows[0].allocation.unresolved_quantity == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("unit,amount,expected_rows,remainder", [("classes", 2, 2, 1), ("credits", 6, 2, 3)])
async def test_insufficient_options_keep_an_explicit_remainder(catalog, unit, amount, expected_rows, remainder):
    catalog("CS435", 3)
    generated, rows, _ = await allocate(amount, unit, ["CS435"])
    assert len(rows) == expected_rows
    assert [r.course_code for r in rows].count("CS435") == 1
    assert any(r.course_code == "TBD" for r in rows)
    assert all(r.allocation.status == "partial" and r.allocation.unresolved_quantity == remainder for r in rows)
    assert any("unresolved" in warning.lower() for warning in generated.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("unit,amount,expected_rows", [("classes", 2, 2), ("credits", 6, 1), ("credits", 1.5, 1)])
async def test_universal_or_unreadable_requirements_preserve_their_entire_remainder(catalog, unit, amount, expected_rows):
    _, rows, _ = await allocate(amount, unit, ["@"])
    assert len(rows) == expected_rows
    assert all(r.course_code == "TBD" and r.allocation.unresolved_quantity == amount for r in rows)
    if unit == "credits":
        assert rows[0].credits == amount and rows[0].credits_estimated


@pytest.mark.asyncio
@pytest.mark.parametrize("status,credits", [("missing", None), ("unverified", 3), ("variable", 6)])
async def test_estimated_credits_never_certify_a_credit_requirement(catalog, status, credits):
    catalog("CS435", credits, status)
    _, rows, _ = await allocate(6, "credits", ["CS435"])
    assert len(rows) == 1  # Don't invent an extra six credits beside an uncertain course.
    assert rows[0].allocation.allocated_quantity == 0
    assert rows[0].allocation.unresolved_quantity == 6
    assert rows[0].allocation.status == "partial"


@pytest.mark.asyncio
async def test_verified_alternatives_are_used_before_uncertain_automatic_choices(catalog):
    catalog("CS480", 3)
    catalog("CS490", 3)
    generated, rows, _ = await allocate(6, "credits", ["CS435", "CS480", "CS490"])
    assert {r.course_code for r in rows} == {"CS480", "CS490"}
    assert rows[0].allocation.unresolved_quantity == 0
    assert not any("CS435:" in warning for warning in generated.warnings)
    _, requested, _ = await allocate(6, "credits", ["CS435", "CS480", "CS490"], electives=["CS435"])
    assert [r.course_code for r in requested] == ["CS435"]
    assert requested[0].allocation.unresolved_quantity == 6


@pytest.mark.asyncio
async def test_zero_and_unknown_quantities_are_distinct(catalog):
    catalog("CS435")
    _, zero, extras = await allocate(0, "classes", ["CS435"], electives=["CS435"])
    assert not zero and [r.course_code for r in extras] == ["CS435"]
    _, unknown, _ = await allocate(None, "classes", ["CS435"])
    assert len(unknown) == 1 and unknown[0].allocation.status == "unknown"
    assert unknown[0].allocation.unresolved_quantity is None


@pytest.mark.asyncio
async def test_completed_and_in_progress_options_are_not_reused(catalog):
    for code in ["CS435", "CS480", "CS490"]:
        catalog(code)
    _, rows, _ = await allocate(2, "classes", ["CS435", "CS480", "CS490"],
                                completed_courses=["CS435"], in_progress_courses=["CS480"])
    assert {r.course_code for r in rows} == {"CS490", "TBD"}
    assert rows[0].allocation.unresolved_quantity == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["UNREADABLE", "CS4X", "CS١٢٣"])
async def test_posted_nonconcrete_options_cannot_count_as_allocated_classes(catalog, option):
    _, rows, _ = await allocate(1, "classes", [option])
    assert [r.course_code for r in rows] == ["TBD"]
    assert rows[0].allocation.allocated_quantity == 0
    assert rows[0].allocation.unresolved_quantity == 1


@pytest.mark.asyncio
async def test_added_courses_keep_prerequisite_ordering(catalog):
    catalog("CS435", 3)
    catalog("CS480", 3, prerequisites=["CS435"])
    generated, rows, _ = await allocate(2, "classes", ["CS435", "CS480"])
    placed = {r.course_code: i for i, s in enumerate(generated.semesters) for r in s.courses}
    assert placed["CS435"] < placed["CS480"]
    assert all(r.allocation.allocated_quantity == 2 for r in rows)
    assert asdict(generated)["semesters"][0]["courses"][0]["allocation"] is not None


@pytest.mark.asyncio
async def test_extreme_quantity_has_bounded_expansion_and_visible_remainder(catalog):
    generated, rows, _ = await allocate(1_000_000, "classes", [])
    assert 1 <= len(rows) <= 200
    assert rows[0].allocation.unresolved_quantity == 1_000_000
    assert any("limit" in warning.lower() for warning in generated.warnings)


@pytest.mark.asyncio
async def test_credit_placeholders_respect_the_target_and_keep_decimal_remainder(catalog):
    generated, rows, _ = await allocate(7.5, "credits", [], credit_target=3)
    assert [r.credits for r in rows] == [3, 3, 1.5]
    assert all(r.allocation.unresolved_quantity == 7.5 for r in rows)
    assert all(s.total_credits <= 3 for s in generated.semesters)


@pytest.mark.asyncio
async def test_oversized_courses_preserve_allocation_in_force_add_packing(catalog):
    catalog("CS435", 4)
    catalog("CS480", 4)
    _, rows, _ = await allocate(2, "classes", ["CS435", "CS480"], credit_target=3)
    assert len(rows) == 2 and all(r.allocation.allocated_quantity == 2 for r in rows)


@pytest.mark.asyncio
async def test_only_needed_electives_are_allocated_and_zero_credit_candidates_terminate(catalog):
    for code, credits in [("CS435", 0), ("CS480", 3), ("CS490", 3)]:
        catalog(code, credits)
    _, rows, _ = await allocate(6, "credits", ["CS435", "CS480", "CS490"])
    assert {r.course_code for r in rows} == {"CS435", "CS480", "CS490"}
    assert rows[0].allocation.unresolved_quantity == 0
    _, rows, extras = await allocate(1, "classes", ["CS4XX"], electives=["CS480", "CS490"])
    assert len(rows) == len(extras) == 1
    assert rows[0].allocation.allocated_quantity == 1 and extras[0].allocation is None


def test_real_generate_http_response_includes_shared_progress_and_distinct_slots(api, catalog):
    catalog("CS435", 4)
    catalog("CS480", 2)
    client, result = api
    result.mappings.return_value = []
    response = client.post("/api/plan/generate", json={
        "parsed_degree": {"majors": ["Synthetic"], "credits_remaining": 6, "still_needed": [{
            "requirement_id": "req-http", "requirement": "Elective", "options": ["CS435", "CS480"],
            "remaining_quantity": 6, "quantity_unit": "credits",
        }]}, "preferences": {},
    })
    assert response.status_code == 200, response.text
    rows = [r for s in response.json()["semesters"] for r in s["courses"]]
    assert len(rows) == len({r["slot_id"] for r in rows}) == 2
    assert all(r["allocation"] == {"required_quantity": 6, "allocated_quantity": 6,
                                   "unresolved_quantity": 0, "quantity_unit": "credits", "status": "allocated"} for r in rows)


from tests.deployment.test_api_contracts import api  # noqa: E402,F401
