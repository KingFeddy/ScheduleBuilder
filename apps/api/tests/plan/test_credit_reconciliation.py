"""Reconcile final schedule credits using synthetic audits and catalog data."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import ParsedDegree, PlanPreferences
from src.services import plan
from tests.plan.test_course_allocation import requirement
from tests.plan.test_requirement_allocation import catalog  # noqa: F401


async def generate(requirements, remaining, electives=(), credit_target=15, **history):
    result = MagicMock()
    result.mappings.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    audit = plan.validate_parsed_degree(ParsedDegree(
        majors=["Synthetic"], still_needed=requirements, credits_remaining=remaining, **history,
    ))
    return await plan.generate_plan(audit, PlanPreferences(courses=list(electives), credits_per_semester=credit_target), session)


def review(generated):
    return next(w for w in generated.warnings if w.startswith("Credit review:"))


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,direction", [(2, "1 above"), (4, "1 below")])
async def test_small_overage_and_shortfall_are_reported(catalog, remaining, direction):
    catalog("CS435", 3)
    generated = await generate([requirement("core", ["CS435"])], remaining)
    assert f"Requirement-linked credits are {direction} the audit figure" in review(generated)
    assert "double-count" not in review(generated)


@pytest.mark.asyncio
async def test_unknown_audit_total_is_not_zero(catalog):
    catalog("CS435", 9)
    generated = await generate([requirement("core", ["CS435"])], None)
    assert "Audit remaining credits are unknown" in review(generated)
    assert "above" not in review(generated) and "below" not in review(generated)
    assert not any("listed as 0" in w for w in generated.warnings)


@pytest.mark.asyncio
async def test_requested_extras_do_not_inflate_requirement_discrepancy(catalog):
    catalog("CS435", 3)
    catalog("CS480", 9)
    generated = await generate([requirement("core", ["CS435"])], 3, ["CS480"])
    message = review(generated)
    assert "12 scheduled credits" in message
    assert "3 selected for requirements" in message
    assert "9 additional elective credits" in message
    assert "above" not in message and "below" not in message


@pytest.mark.asyncio
async def test_final_load_fillers_are_included_in_schedule_but_not_requirements(catalog):
    catalog("HSS404", 3)
    item = requirement("capstone", ["HSS404"])
    item.requirement = "Synthetic senior seminar"
    generated = await generate([item], 3)
    assert sum(s.total_credits for s in generated.semesters) == 12
    message = review(generated)
    assert "12 scheduled credits" in message and "9 course-load filler credits" in message
    assert "above" not in message and "below" not in message


@pytest.mark.asyncio
async def test_matching_placeholder_total_is_still_partial(catalog):
    generated = await generate([requirement("unreadable", [], 2)], 6)
    assert "0 selected for requirements" in review(generated)
    assert "6 unresolved slot credits" in review(generated)
    assert "6 estimated credits" in review(generated)
    assert any(w.startswith("Partial plan: 1 audit requirement has unresolved allocation") for w in generated.warnings)


@pytest.mark.asyncio
async def test_estimated_selected_credits_and_unknown_quantities_remain_unverified(catalog):
    generated = await generate([requirement("unknown", ["CS435"], None)], 3)
    assert "3 selected for requirements" in review(generated)
    assert "3 estimated credits" in review(generated)
    assert any(w.startswith("Partial plan:") for w in generated.warnings)


@pytest.mark.asyncio
async def test_overlap_is_counted_once_and_history_does_not_reduce_audit_total_again(catalog):
    catalog("CS435", 3)
    catalog("CS480", 3)
    generated = await generate([
        requirement("major", ["CS435"]), requirement("minor", ["CS435"]),
    ], 6, ["CS480"], completed_courses=["CS480"], in_progress_courses=["CS490"])
    message = review(generated)
    assert "3 selected for requirements" in message and "3 unresolved slot credits" in message
    assert "0 additional elective credits" in message and "audit lists 6 remaining credits" in message
    assert any(w.startswith("Partial plan: 1 audit requirement") for w in generated.warnings)


@pytest.mark.asyncio
async def test_verified_matching_allocation_needs_no_credit_review(catalog):
    catalog("CS435", 1.5)
    catalog("CS480", 1.5)
    generated = await generate([requirement("core", ["CS435", "CS480"], 2)], 3)
    assert not any(w.startswith(("Credit review:", "Partial plan:")) for w in generated.warnings)


@pytest.mark.asyncio
async def test_sample_arithmetic_separates_39_requirement_credits_from_48_scheduled(catalog):
    # Synthetic reproduction of the totals, not a reconstruction of a personal PDF.
    items = []
    for index in range(11):
        code = f"CS{300 + index}"
        catalog(code, 3)
        items.append(requirement(f"core-{index}", [code]))
    items.append(requirement("unknown-choice", [], 1))
    catalog("HSS404", 3)
    capstone = requirement("capstone", ["HSS404"])
    capstone.requirement = "Synthetic senior seminar"
    items.append(capstone)
    generated = await generate(items, 24, credit_target=12)
    message = review(generated)
    assert sum(s.total_credits for s in generated.semesters) == 48
    assert "audit lists 24 remaining credits" in message
    assert "48 scheduled credits: 36 selected for requirements, 3 unresolved slot credits" in message
    assert "9 course-load filler credits" in message
    assert "12 estimated credits" in message
    assert "Requirement-linked credits are 15 above the audit figure" in message
