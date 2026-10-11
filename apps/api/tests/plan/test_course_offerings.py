"""Seasonal offerings constrain placement without guessing from missing sections."""
import pytest

from src.schemas.plan import ParseValidationError
from src.services.course_offerings import COURSE_OFFERINGS, OfferingPattern
from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def offering(monkeypatch, code, *seasons):
    monkeypatch.setitem(COURSE_OFFERINGS, code, OfferingPattern(frozenset(seasons), "Synthetic test rule"))


@pytest.mark.asyncio
async def test_spring_only_course_waits_while_unknown_offerings_remain_eligible():
    plan, terms, _ = await generate(['PHYS485', 'CS100'], [], target=6)
    assert terms == {'CS100': '202690', 'PHYS485': '202710'}
    assert sum(s.total_credits for s in plan.semesters) == 6


@pytest.mark.asyncio
async def test_spring_prerequisite_delays_spring_only_successor_a_full_year(monkeypatch):
    offering(monkeypatch, 'PHYS200', '10')
    _, terms, _ = await generate(['PHYS485', 'PHYS200'], [stored('PHYS485', prereq=condition('PHYS200'))])
    assert terms == {'PHYS200': '202710', 'PHYS485': '202810'}


@pytest.mark.asyncio
async def test_oversized_concurrent_group_waits_for_shared_season():
    plan, terms, _ = await generate(['PHYS485', 'CS100'], [coreq('PHYS485', 'CS100')], target=3)
    assert set(terms.values()) == {'202710'}
    assert len(plan.semesters) == 1 and plan.semesters[0].total_credits == 6


@pytest.mark.asyncio
async def test_incompatible_concurrent_seasons_fail_without_hanging(monkeypatch):
    offering(monkeypatch, 'CS100', '90')
    with pytest.raises(ParseValidationError, match='no shared offering semester'):
        await generate(['PHYS485', 'CS100'], [coreq('PHYS485', 'CS100')])


@pytest.mark.asyncio
async def test_final_seminar_with_spring_companion_is_not_added_to_fall():
    plan, terms, _ = await generate(['HSS404', 'PHYS485', 'CS100'], [coreq('HSS404', 'PHYS485')], target=9)
    assert terms['CS100'] == '202690'
    assert terms['HSS404'] == terms['PHYS485'] == plan.semesters[-1].term == '202710'


@pytest.mark.asyncio
async def test_fall_only_final_seminar_waits_past_last_spring_course(monkeypatch):
    offering(monkeypatch, 'HSS404', '90')
    plan, terms, _ = await generate(['HSS404', 'PHYS485'], [], target=6)
    assert terms == {'PHYS485': '202710', 'HSS404': '202790'}
    assert plan.semesters[-1].term == terms['HSS404']
