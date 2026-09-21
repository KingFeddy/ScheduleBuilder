"""Explicit same-semester prerequisite conditions survive selection and packing."""
import pytest

from src.schemas.plan import CourseAttempt
from src.schemas.prerequisites import AllConditions, UnresolvedCondition
from tests.plan.test_corequisite_alternatives import choice, concurrent
from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_flexible_prerequisites import flexible
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


@pytest.mark.asyncio
async def test_concurrent_prerequisite_replaces_legacy_prior_edge():
    plan, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=concurrent('CS100'))],
                                   legacy={'CS200': ['CS100']}, target=3)
    assert terms['CS200'] == terms['CS100']
    assert plan.semesters[0].total_credits == 6
    assert any('concurrent prerequisite group' in warning and 'above your' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_all_three_timings_keep_group_and_external_dependencies():
    rule = AllConditions(items=[condition('CS100'), concurrent('CS200'), flexible('CS300')])
    _, terms, _ = await generate(['CS400', 'CS200', 'CS300', 'CS100'], [stored('CS400', prereq=rule)], target=6)
    assert terms['CS100'] < terms['CS400'] == terms['CS200']
    assert terms['CS300'] <= terms['CS400']


@pytest.mark.asyncio
async def test_prerequisite_and_corequisite_groups_merge_transitively():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        stored('CS100', prereq=concurrent('CS200')), coreq('CS200', 'CS300'),
    ], target=6)
    assert len(set(terms.values())) == 1


@pytest.mark.asyncio
async def test_mandatory_prerequisite_group_constrains_corequisite_or_credit_search():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        choice('CS100', 'CS200', 'CS300'), stored('CS200', prereq=concurrent('CS400')),
    ], target=6)
    assert terms['CS100'] == terms['CS300']
    assert terms['CS200'] == terms['CS400'] != terms['CS100']


@pytest.mark.asyncio
async def test_corequisite_or_avoids_cycle_through_mandatory_prerequisite_group():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        stored('CS100', prereq=condition('CS300')),
        stored('CS200', prereq=concurrent('CS100')),
        choice('CS300', 'CS200', 'CS400'),
    ], target=6)
    assert terms['CS300'] == terms['CS400'] < terms['CS100'] == terms['CS200']


@pytest.mark.asyncio
async def test_concurrent_prerequisite_crosses_capstone_boundary():
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [
        stored('CS200', prereq=concurrent('CS100')), stored('CS300', prereq=condition('CS200')),
    ], senior={'CS100'}, target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']
    assert terms['CS100'] == '202690'


@pytest.mark.asyncio
async def test_missing_concurrent_prerequisite_is_explicit_without_new_course():
    plan, terms, _ = await generate(['CS200'], [stored('CS200', prereq=concurrent('CS100'))])
    assert set(terms) == {'CS200'}
    assert any(warning.startswith('Partial plan:') and 'concurrent prerequisite' in warning
               and 'CS100' in warning for warning in plan.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize('term', ['Spring 2026', 'Fall 2026'])
async def test_completed_history_does_not_replace_selected_same_term_course(term):
    plan, terms, _ = await generate(['CS200'], [stored('CS200', prereq=concurrent('CS100'))], attempts=[
        CourseAttempt(course_code='CS100', grade='A', term=term, credits=3),
    ])
    assert set(terms) == {'CS200'}
    assert any(warning.startswith('Partial plan:') and 'concurrent prerequisite' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_conflicting_prior_and_concurrent_conditions_remain_partial():
    rule = AllConditions(items=[condition('CS100'), concurrent('CS100')])
    plan, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)], target=6)
    assert terms['CS100'] < terms['CS200']
    assert any(warning.startswith('Partial plan:') and 'concurrent prerequisite grouping' in warning
               and 'conflict' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_indirect_prior_conflict_rejects_group_without_hanging():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        stored('CS100', prereq=concurrent('CS300')), stored('CS200', prereq=condition('CS100')),
        stored('CS300', prereq=condition('CS200')),
    ], target=3)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']
    assert any('concurrent prerequisite grouping' in warning and 'conflicts' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_planned_grade_and_required_section_remain_unverified():
    rule = concurrent('CS100').model_copy(update={'minimum_grade': 'B', 'required_crn': '12345'})
    plan, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)], target=3)
    assert terms['CS100'] == terms['CS200']
    assert any('minimum grade B' in warning and 'required section 12345' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_unsupported_sibling_does_not_get_dropped_to_establish_group():
    rule = AllConditions(items=[concurrent('CS100'), UnresolvedCondition(reason='Permission required')])
    _, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)], target=3)
    assert terms['CS100'] != terms['CS200']


@pytest.mark.asyncio
async def test_earlier_history_discharges_prior_sibling_but_keeps_concurrent_group():
    rule = AllConditions(items=[condition('CS100').model_copy(update={'minimum_grade': 'C'}), concurrent('CS200')])
    plan, terms, _ = await generate(['CS300', 'CS200'], [stored('CS300', prereq=rule)], attempts=[
        CourseAttempt(course_code='CS100', grade='B', term='Spring 2026', credits=3),
    ], target=3)
    assert terms['CS300'] == terms['CS200']
    assert 'CS100' not in terms
    assert not any('lack qualifying history' in warning or 'ordering for CS300' in warning for warning in plan.warnings)
