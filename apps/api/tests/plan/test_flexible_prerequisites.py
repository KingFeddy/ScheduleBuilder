"""Mandatory prior-or-concurrent rules use actual placements and honest history."""
import pytest

from src.schemas.plan import CourseAttempt
from src.schemas.prerequisites import AllConditions, AnyConditions, UnresolvedCondition
from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def flexible(code):
    return condition(code, timing='prior_or_concurrent')


@pytest.mark.asyncio
async def test_flexible_rule_replaces_stale_strict_edge_and_shares_semester():
    _, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=flexible('CS100'))],
                                 legacy={'CS200': ['CS100']}, target=6)
    assert terms['CS200'] == terms['CS100']


@pytest.mark.asyncio
async def test_credit_contention_places_flexible_dependency_earlier():
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [
        stored('CS300', prereq=flexible('CS200')), stored('CS200', prereq=flexible('CS100')),
    ], target=3)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_retries_newly_eligible_courses_within_same_semester():
    plan, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [
        stored('CS300', prereq=flexible('CS200')), stored('CS200', prereq=flexible('CS100')),
    ], target=9)
    assert len(set(terms.values())) == 1
    assert plan.semesters[0].total_credits == 9


@pytest.mark.asyncio
async def test_mixed_and_retains_strict_prior_condition():
    rule = AllConditions(items=[flexible('CS100'), condition('CS200')])
    _, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [stored('CS300', prereq=rule)], target=9)
    assert terms['CS200'] < terms['CS300'] and terms['CS100'] <= terms['CS300']


@pytest.mark.asyncio
async def test_repeated_course_conditions_use_stricter_prior_timing():
    rule = AllConditions(items=[flexible('CS100'), condition('CS100')])
    _, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)], target=6)
    assert terms['CS100'] < terms['CS200']


@pytest.mark.asyncio
async def test_mutual_flexible_requirements_form_atomic_group_above_target():
    plan, terms, _ = await generate(['CS100', 'CS200'], [
        stored('CS100', prereq=flexible('CS200')), stored('CS200', prereq=flexible('CS100')),
    ], target=3)
    assert terms['CS100'] == terms['CS200']
    assert plan.semesters[0].total_credits == 6
    assert any('concurrent prerequisite group' in warning and 'above your' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_flexible_cycle_combines_existing_corequisite_groups():
    first = coreq('CS100', 'CS200')
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [first,
        stored('CS200', prereq=flexible('CS300')), stored('CS300', prereq=flexible('CS100')),
    ], target=6)
    assert len(set(terms.values())) == 1


@pytest.mark.asyncio
async def test_strict_edge_in_flexible_cycle_is_partial_without_hanging():
    plan, terms, _ = await generate(['CS100', 'CS200'], [
        stored('CS100', prereq=flexible('CS200')), stored('CS200', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS100'] < terms['CS200']
    assert any(warning.startswith('Partial plan:') and 'prior-or-concurrent' in warning
               and 'conflict' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_group_member_waits_for_external_flexible_dependency():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [coreq('CS100', 'CS200'),
        stored('CS200', prereq=flexible('CS300')),
    ], target=6)
    assert terms['CS300'] < terms['CS100'] == terms['CS200']


@pytest.mark.asyncio
async def test_group_with_strict_conflict_keeps_corequisites_and_reports_flexible_gap():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [coreq('CS100', 'CS200'),
        stored('CS200', prereq=flexible('CS300')), stored('CS300', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']
    assert any(warning.startswith('Partial plan:') and 'prior-or-concurrent' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_flexible_dependency_crosses_capstone_phase_and_preserves_descendants():
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [
        stored('CS200', prereq=flexible('CS100')),
    ], legacy={'CS300': ['CS200']}, senior={'CS100'}, target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']
    assert terms['CS100'] == '202690'


@pytest.mark.asyncio
async def test_flexible_dependency_can_top_up_existing_normal_semester():
    plan, terms, _ = await generate(['CS100', 'CS200'], [stored('CS200', prereq=flexible('CS100'))],
                                   senior={'CS200'}, target=6)
    assert terms['CS100'] == terms['CS200']
    assert len(plan.semesters) == 1 and plan.semesters[0].total_credits == 6


@pytest.mark.asyncio
@pytest.mark.parametrize('grade, partial', [('B', False), ('D', True), ('IP', True)])
async def test_history_must_establish_grade_and_timing(grade, partial):
    rule = flexible('CS100').model_copy(update={'minimum_grade': 'C'})
    plan, terms, _ = await generate(['CS200'], [stored('CS200', prereq=rule)], attempts=[
        CourseAttempt(course_code='CS100', grade=grade, term='Spring 2026', credits=3),
    ])
    assert set(terms) == {'CS200'}
    assert any(warning.startswith('Partial plan: prior-or-concurrent') for warning in plan.warnings) == partial


@pytest.mark.asyncio
async def test_missing_flexible_course_is_reported_without_invented_course():
    plan, terms, _ = await generate(['CS200'], [stored('CS200', prereq=flexible('CS100'))])
    assert set(terms) == {'CS200'}
    assert any(warning.startswith('Partial plan: prior-or-concurrent') and 'CS100' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_unsupported_branch_preserves_legacy_fallback():
    rule = AllConditions(items=[flexible('CS100'), UnresolvedCondition(reason='Unknown rule')])
    _, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)],
                                 legacy={'CS200': ['CS100']})
    assert terms['CS100'] < terms['CS200']


@pytest.mark.asyncio
async def test_flexible_or_is_not_flattened_into_all_dependencies():
    rule = AnyConditions(items=[flexible('CS100'), flexible('CS200')])
    _, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [stored('CS300', prereq=rule)], target=3)
    assert terms['CS300'] == '202690'  # Remains diagnostic-only until mixed OR selection is implemented.


@pytest.mark.asyncio
async def test_failed_source_keeps_legacy_ordering():
    row = stored('CS200', prereq=flexible('CS100'), status='failed')
    _, terms, _ = await generate(['CS200', 'CS100'], [row], legacy={'CS200': ['CS100']})
    assert terms['CS100'] < terms['CS200']


@pytest.mark.asyncio
async def test_flexible_rule_node_limit_retains_fallback_and_reports_limit(monkeypatch):
    from src.services import flexible_prerequisites
    monkeypatch.setattr(flexible_prerequisites, 'MAX_FLEXIBLE_RULE_NODES', 1)
    rule = AllConditions(items=[flexible('CS100')])
    plan, terms, _ = await generate(['CS200', 'CS100'], [stored('CS200', prereq=rule)],
                                   legacy={'CS200': ['CS100']}, target=6)
    assert terms['CS100'] < terms['CS200']
    assert any('expansion limit' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_unknown_history_timing_does_not_discharge_flexible_requirement():
    plan, _, _ = await generate(['CS200'], [stored('CS200', prereq=flexible('CS100'))], attempts=[
        CourseAttempt(course_code='CS100', grade='A', credits=3),
    ])
    assert any(warning.startswith('Partial plan: prior-or-concurrent') for warning in plan.warnings)
