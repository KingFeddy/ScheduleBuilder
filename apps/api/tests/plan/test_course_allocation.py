"""One scheduled occurrence per course; overlapping audit rows stay reviewable."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import ParsedDegree, PlanPreferences, StillNeededItem
from src.services import plan
from tests.plan.test_requirement_allocation import catalog  # noqa: F401


def requirement(identity, options, quantity=1, unit="classes"):
    return StillNeededItem(requirement_id=identity, requirement=f"Synthetic {identity}",
                           options=options, remaining_quantity=quantity, quantity_unit=unit)


async def generate(requirements, electives=(), **history):
    result = MagicMock()
    result.mappings.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    degree = plan.validate_parsed_degree(ParsedDegree(majors=["Synthetic major"], minors=["Synthetic minor"],
        credits_remaining=24, still_needed=requirements, **history))
    generated = await plan.generate_plan(degree, PlanPreferences(courses=list(electives)), session)
    rows = [course for semester in generated.semesters for course in semester.courses]
    concrete = [row for row in rows if row.course_code not in ("TBD", "FREE")]
    assert len(concrete) == len({row.course_code for row in concrete})
    assert len(rows) == len({row.slot_id for row in rows})
    return generated, rows, concrete


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity,unit", [(1, "classes"), (4, "credits"), (None, "classes")])
async def test_major_minor_overlap_is_once_with_a_reviewable_unresolved_row(catalog, quantity, unit):
    catalog("CS435", 4)
    generated, rows, concrete = await generate([
        requirement("major", ["CS435"], quantity, unit), requirement("minor", ["CS435"], quantity, unit)])
    assert len(concrete) == 1 and sum(row.credits for row in concrete) == 4
    blocked = next(row for row in rows if row.course_code == "TBD")
    assert blocked.requirement.requirement_id == "minor"
    assert "CS435" in blocked.reason and "sharing" in blocked.reason.lower()
    assert "Synthetic major" in blocked.reason
    assert any("sharing" in message.lower() for message in generated.warnings)
    if quantity is not None:
        assert blocked.allocation.allocated_quantity == 0
        assert blocked.allocation.unresolved_quantity == quantity
    else:
        assert blocked.allocation.status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_flexible_choice_does_not_take_the_only_option_from_required_course(catalog, reverse):
    catalog("CS435", 4)
    catalog("CS480", 3)
    requirements = [requirement("flexible", ["CS435", "CS480"]), requirement("mandatory", ["CS435"])]
    if reverse:
        requirements.reverse()
    _, rows, concrete = await generate(requirements)
    assert {r.requirement.requirement_id: r.course_code for r in concrete} == {"mandatory": "CS435", "flexible": "CS480"}
    assert all(row.allocation.status == "allocated" for row in rows if row.requirement)


@pytest.mark.asyncio
async def test_quantity_expansion_respects_other_requirement_ownership(catalog):
    for code in ["CS435", "CS480", "CS490"]:
        catalog(code)
    _, rows, concrete = await generate([
        requirement("electives", ["CS435", "CS480", "CS490"], 2), requirement("core", ["CS480"])])
    assert len(concrete) == 3
    assert next(r for r in rows if r.course_code == "CS480").requirement.requirement_id == "core"
    assert all(r.allocation.unresolved_quantity == 0 for r in rows if r.requirement)


@pytest.mark.asyncio
@pytest.mark.parametrize("electives", [[], ["CS435"], ["CS435", "CS480"]])
async def test_requested_course_is_not_duplicated_by_a_second_requirement_or_extra(catalog, electives):
    catalog("CS435")
    catalog("CS480")
    _, rows, concrete = await generate([
        requirement("core", ["CS435"]), requirement("elective", ["CS435", "CS480"])], electives)
    assert {r.course_code for r in concrete} == {"CS435", "CS480"}
    assert all(r.requirement is not None for r in concrete)
    assert all(r.allocation.status == "allocated" for r in rows if r.requirement)


@pytest.mark.asyncio
async def test_requested_wildcards_do_not_share_courses_with_core_or_other_wildcards(catalog):
    catalog("CS435")
    catalog("CS480")
    generated, rows, concrete = await generate([
        requirement("major-electives", ["CS4XX"], 2), requirement("core", ["CS435"]),
        requirement("minor-elective", ["@"], 1)], ["CS435", "CS480"])
    assert {r.course_code for r in concrete} == {"CS435", "CS480"}
    assert next(r for r in concrete if r.course_code == "CS435").requirement.requirement_id == "core"
    assert any("sharing" in message.lower() for message in generated.warnings)


@pytest.mark.asyncio
async def test_same_credit_course_cannot_count_toward_two_credit_requirements(catalog):
    catalog("CS435", 4)
    catalog("CS480", 2)
    _, rows, concrete = await generate([
        requirement("core", ["CS435"], 4, "credits"),
        requirement("electives", ["CS435", "CS480"], 6, "credits")])
    assert sum(r.credits for r in concrete) == 6
    elective = next(r for r in rows if r.requirement and r.requirement.requirement_id == "electives")
    assert elective.allocation.allocated_quantity == 2
    assert elective.allocation.unresolved_quantity == 4


@pytest.mark.asyncio
async def test_zero_and_history_exclusions_do_not_claim_courses(catalog):
    for code in ["CS435", "CS480", "CS490"]:
        catalog(code)
    _, rows, concrete = await generate([
        requirement("zero", ["CS435"], 0), requirement("active", ["CS435", "CS480", "CS490"], 2)],
        completed_courses=["CS480"], in_progress_courses=["CS490"])
    assert [r.course_code for r in concrete] == ["CS435"]
    assert not any(r.requirement and r.requirement.requirement_id == "zero" for r in rows)


@pytest.mark.asyncio
async def test_unknown_quantity_cannot_duplicate_another_selected_course(catalog):
    catalog("CS435")
    catalog("CS480")
    _, rows, concrete = await generate([
        requirement("known", ["CS435"]), requirement("unknown", ["CS435", "CS480"], None)])
    assert {r.course_code for r in concrete} == {"CS435", "CS480"}
    assert next(r for r in rows if r.requirement and r.requirement.requirement_id == "unknown").allocation.status == "unknown"


@pytest.mark.asyncio
async def test_deduplication_preserves_prerequisite_links_and_stable_regeneration(catalog):
    catalog("CS435", 3)
    catalog("CS480", 3, prerequisites=["CS435"])
    requirements = [requirement("major", ["CS435"]), requirement("minor", ["CS435"]),
                    requirement("advanced", ["CS480"])]
    generated, rows, _ = await generate(requirements)
    placed = {r.course_code: i for i, semester in enumerate(generated.semesters) for r in semester.courses if r.course_code != "TBD"}
    assert placed["CS435"] < placed["CS480"]
    _, again, _ = await generate(requirements)
    assert [(r.course_code, r.slot_id, r.allocation) for r in rows] == [(r.course_code, r.slot_id, r.allocation) for r in again]


@pytest.mark.asyncio
async def test_overlap_note_survives_metadata_refresh_for_uncertain_requested_choice(catalog):
    catalog("CS435", 3)
    generated, rows, _ = await generate([
        requirement("core", ["CS435"], 3, "credits"),
        requirement("elective", ["CS435", "CS480"], 6, "credits")], ["CS480"])
    row = next(r for r in rows if r.course_code == "CS480")
    assert row.allocation.unresolved_quantity == 6
    assert "Sharing" in row.reason and "CS435" in row.reason


def test_http_cannot_enable_unverified_sharing_from_client_flags(api, catalog):
    catalog("CS435", 4)
    client, result = api
    result.mappings.return_value = []
    requirements = [requirement("major", ["CS435"], 4, "credits").model_dump(),
                    requirement("minor", ["CS435"], 4, "credits").model_dump()]
    for item in requirements:
        item["allow_sharing"] = True  # No verified policy exists in this contract.
    response = client.post("/api/plan/generate", json={
        "parsed_degree": {"majors": ["Synthetic"], "minors": ["Synthetic minor"],
                          "credits_remaining": 8, "still_needed": requirements}, "preferences": {}})
    assert response.status_code == 200, response.text
    rows = [r for s in response.json()["semesters"] for r in s["courses"]]
    assert [r["course_code"] for r in rows].count("CS435") == 1
    assert next(r for r in rows if r["course_code"] == "TBD")["allocation"]["unresolved_quantity"] == 4
    assert any("Sharing" in warning for warning in response.json()["warnings"])


from tests.deployment.test_api_contracts import api  # noqa: E402,F401
