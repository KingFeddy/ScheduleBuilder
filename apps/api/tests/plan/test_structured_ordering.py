"""Verified prior-course rules drive actual placement without inventing history."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import CourseAttempt, ParsedDegreeValidated, PlanPreferences, StillNeededItem
from src.schemas.prerequisites import AllConditions
from src.services.plan import generate_plan
from tests.plan.test_prerequisite_checks import condition, stored


async def generate(codes, rows, *, legacy=None, attempts=None, target=6, senior=()):
    history=ParsedDegreeValidated(majors=['Synthetic'],course_attempts=attempts,still_needed=[
        StillNeededItem(requirement=f'Senior Project {code}' if code in senior else f'Course {code}',
                        options=[code],remaining_quantity=1,quantity_unit='classes') for code in codes
    ])
    def execute(statement, params):
        result=MagicMock()
        if 'prerequisites_rules' in str(statement):
            result.mappings.return_value=rows
        elif 'FROM courses' in str(statement):
            result.mappings.return_value=[{'course_code':code,'title':code,'credits':3,
                                          'prerequisites':(legacy or {}).get(code,[])} for code in params['codes']]
        else:result.mappings.return_value=[]
        return result
    session=AsyncMock()
    session.execute.side_effect=execute
    plan=await generate_plan(history,PlanPreferences(courses=[],credits_per_semester=target,start_term='202690'),session)
    terms={c.course_code:s.term for s in plan.semesters for c in s.courses}
    return plan,terms,session


@pytest.mark.asyncio
async def test_verified_chain_replaces_empty_legacy_arrays_and_follows_actual_placement():
    plan,terms,session=await generate(['CS300','CS200','CS100','CS400'],[
        stored('CS300',prereq=condition('CS200')),stored('CS200',prereq=condition('CS100')),
    ],target=3)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']
    assert all(s.total_credits <= 3 for s in plan.semesters)
    assert sum('prerequisites_rules' in str(call.args[0]) for call in session.execute.call_args_list) == 1


@pytest.mark.asyncio
async def test_all_requires_both_selected_prerequisites_before_dependent():
    _,terms,_=await generate(['CS300','CS100','CS200'],[
        stored('CS300',prereq=AllConditions(items=[condition('CS100'),condition('CS200')])),
    ])
    assert terms['CS100'] < terms['CS300'] and terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_verified_empty_removes_obsolete_legacy_edge():
    _,terms,_=await generate(['CS200','CS100'],[
        stored('CS200',status='verified_empty',prereq=AllConditions(items=[])),
    ],legacy={'CS200':['CS100']})
    assert terms['CS200'] == terms['CS100']


@pytest.mark.asyncio
async def test_verified_dependency_crossing_capstone_boundary_is_preserved_transitively():
    plan,terms,_=await generate(['CS300','CS200','CS100'],[
        stored('CS200',prereq=condition('CS100')),
    ],legacy={'CS300':['CS200']},senior={'CS100'})
    assert terms['CS100'] < terms['CS200'] < terms['CS300']
    assert terms['CS100'] == '202690'
    assert not any('ordering could not be fully honored' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_supported_history_removes_dependency_but_low_grade_is_partial():
    rule=stored('CS200',prereq=condition('CS100').model_copy(update={'minimum_grade':'C'}))
    for grade,partial in [('B',False),('D',True),('IP',True)]:
        plan,terms,_=await generate(['CS200'],[rule],attempts=[
            CourseAttempt(course_code='CS100',grade=grade,term='Spring 2026',credits=3),
        ])
        assert terms['CS200'] == '202690'
        assert any(w.startswith('Partial plan: prerequisite ordering') for w in plan.warnings) == partial


@pytest.mark.asyncio
async def test_stricter_duplicate_condition_is_not_satisfied_by_weaker_grade():
    rules=AllConditions(items=[condition().model_copy(update={'minimum_grade':'C'}),
                              condition().model_copy(update={'minimum_grade':'B'})])
    plan,_,_=await generate(['CS200'],[stored('CS200',prereq=rules)],attempts=[
        CourseAttempt(course_code='CS100',grade='C',term='Spring 2026',credits=3),
    ])
    assert any(w.startswith('Partial plan: prerequisite ordering') for w in plan.warnings)


@pytest.mark.asyncio
async def test_cycle_and_missing_course_remain_explicit_partial_plans():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[
        stored('CS100',prereq=condition('CS200')),stored('CS200',prereq=condition('CS100')),
        stored('CS300',prereq=condition('CS400')),
    ])
    assert set(terms) == {'CS100','CS200','CS300'}
    assert any(w.startswith('Partial plan: prerequisite ordering') and 'CS300' in w for w in plan.warnings)
    assert any('conflict with recorded rules' in w or 'needs review' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_concurrent_rules_are_not_flattened_into_prior_edges():
    rule = condition('CS100',timing='concurrent')
    plan,terms,_=await generate(['CS200','CS100'],[stored('CS200',prereq=rule)])
    assert terms['CS200'] == terms['CS100']
    assert any('needs review' in w or 'conflict with recorded rules' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_failed_rules_keep_legacy_ordering_with_unverified_notice():
    plan,terms,_=await generate(['CS200','CS100'],[stored('CS200',status='failed')],legacy={'CS200':['CS100']})
    assert terms['CS100'] < terms['CS200']
    assert any('unavailable or unverified' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_structured_capstone_chain_starts_without_artificial_empty_terms():
    _,terms,_=await generate(['CS300','CS200','CS100'],[
        stored('CS300',prereq=condition('CS200')),stored('CS200',prereq=condition('CS100')),
    ],senior={'CS100','CS200','CS300'})
    assert terms == {'CS100':'202690','CS200':'202710','CS300':'202790'}
