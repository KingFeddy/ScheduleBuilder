"""Mandatory concurrent course groups survive packing and remain reviewable."""
import pytest

from src.schemas.prerequisites import AllConditions, AnyConditions
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def coreq(code, *required):
    return stored(code,prereq=AllConditions(items=[]),coreq=AllConditions(items=[
        condition(other,timing='concurrent') for other in required]))


@pytest.mark.asyncio
async def test_corequisites_move_together_under_credit_contention():
    plan,terms,_=await generate(['CS400','CS300','CS100','CS200'],[coreq('CS100','CS200')],target=3)
    assert terms['CS100'] == terms['CS200']
    group=next(s for s in plan.semesters if any(c.course_code=='CS100' for c in s.courses))
    assert {c.course_code for c in group.courses} == {'CS100','CS200'}
    assert group.total_credits == 6
    assert any('corequisite group' in w and '6' in w and '3' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_transitive_groups_are_not_split():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[coreq('CS100','CS200'),coreq('CS200','CS300')],target=6)
    assert len(set(terms.values())) == 1
    assert sum(s.total_credits for s in plan.semesters) == 9
    assert len({c.slot_id for s in plan.semesters for c in s.courses}) == 3


@pytest.mark.asyncio
async def test_group_waits_for_prerequisites_of_every_member():
    rows=[coreq('CS200','CS300'),stored('CS300',prereq=condition('CS100'))]
    _,terms,_=await generate(['CS200','CS300','CS100'],rows,target=6)
    assert terms['CS100'] < terms['CS200'] == terms['CS300']


@pytest.mark.asyncio
async def test_dependent_waits_for_actual_group_placement():
    _,terms,_=await generate(['CS400','CS100','CS200','CS300'],[
        coreq('CS100','CS200'),stored('CS300',prereq=condition('CS200'))],target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_group_spanning_capstone_boundary_and_its_dependents_stays_ordered():
    _,terms,_=await generate(['CS300','CS200','CS100'],[
        coreq('CS100','CS200'),stored('CS300',prereq=condition('CS200'))],senior={'CS100'},target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_group_does_not_partially_top_up_last_normal_semester():
    plan,terms,_=await generate(['CS400','CS100','CS200'],[coreq('CS100','CS200')],senior={'CS100'},target=6)
    assert terms['CS400'] < terms['CS100'] == terms['CS200']
    assert [s.total_credits for s in plan.semesters] == [3,6]


@pytest.mark.asyncio
async def test_missing_corequisite_is_reported_without_invented_course():
    plan,terms,_=await generate(['CS100'],[coreq('CS100','CS200')])
    assert set(terms) == {'CS100'}
    assert any(w.startswith('Partial plan:') and 'corequisite' in w and 'CS200' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_strict_prior_path_inside_group_is_reported_without_hanging():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[
        coreq('CS100','CS300'),stored('CS300',prereq=condition('CS200')),
        stored('CS200',prereq=condition('CS100'))],target=3)
    assert set(terms) == {'CS100','CS200','CS300'}
    assert any(w.startswith('Partial plan:') and 'corequisite' in w and 'prior' in w for w in plan.warnings)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_corequisite_or_is_not_interpreted_as_all_required():
    row=stored('CS100',prereq=AllConditions(items=[]),coreq=AnyConditions(items=[
        condition('CS200',timing='concurrent'),condition('CS300',timing='concurrent')]))
    plan,terms,_=await generate(['CS100','CS200','CS300'],[row],target=3)
    assert len(set(terms.values())) == 3
    assert any('corequisite' in w and ('review' in w or 'conflict' in w) for w in plan.warnings)


@pytest.mark.asyncio
async def test_required_section_stays_unknown_even_when_courses_grouped():
    row=coreq('CS100','CS200')
    row['prerequisites_rules']['corequisites']['items'][0]['required_crn']='12345'
    plan,terms,_=await generate(['CS100','CS200'],[row],target=3)
    assert terms['CS100'] == terms['CS200']
    assert any('required section 12345' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_failed_source_does_not_establish_corequisite_group():
    row=coreq('CS100','CS200');row['prerequisites_status']='failed'
    plan,terms,_=await generate(['CS100','CS200'],[row],target=3)
    assert terms['CS100'] != terms['CS200']
    assert any('unavailable or unverified' in w for w in plan.warnings)
