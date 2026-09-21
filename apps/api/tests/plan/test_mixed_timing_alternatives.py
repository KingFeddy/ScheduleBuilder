"""Prerequisite OR paths retain timing, history and mandatory siblings."""
import pytest

from src.schemas.plan import CourseAttempt
from src.schemas.prerequisites import AllConditions, AnyConditions, UnresolvedCondition
from tests.plan.test_corequisite_alternatives import concurrent
from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_flexible_prerequisites import flexible
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def mixed(code, *rules):
    return stored(code, prereq=AnyConditions(items=list(rules)))


@pytest.mark.asyncio
async def test_uses_available_concurrent_alternative_without_missing_prior_course():
    plan, terms, session = await generate(['CS300', 'CS200'], [
        mixed('CS300', condition('CS100'), concurrent('CS200')),
    ], target=3)
    assert terms['CS300'] == terms['CS200']
    assert not any(w.startswith('Partial plan:') and 'missing' in w for w in plan.warnings)
    assert sum('prerequisites_rules' in str(call.args[0]) for call in session.execute.call_args_list) == 1


@pytest.mark.asyncio
async def test_preserves_required_and_sibling_around_mixed_choice():
    rule = AllConditions(items=[condition('CS100'), AnyConditions(items=[
        concurrent('CS200'), flexible('CS400'),
    ])])
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [stored('CS300', prereq=rule)], target=9)
    assert terms['CS100'] < terms['CS200'] == terms['CS300']


@pytest.mark.asyncio
async def test_flexible_alternative_orders_chain_under_credit_contention():
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [
        mixed('CS300', flexible('CS200'), condition('CS400')),
        stored('CS200', prereq=flexible('CS100')),
    ], target=3)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
@pytest.mark.parametrize('grade, grouped', [('B', False), ('D', True)])
async def test_verified_history_beats_pending_path_but_low_grade_does_not(grade, grouped):
    row = mixed('CS300', condition('CS100').model_copy(update={'minimum_grade': 'C'}), concurrent('CS200'))
    plan, terms, _ = await generate(['CS300', 'CS200'], [row], attempts=[
        CourseAttempt(course_code='CS100', grade=grade, term='Spring 2026', credits=3),
    ], target=3)
    assert (terms['CS300'] == terms['CS200']) == grouped
    assert not any('could not' in w and w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_prior_option_avoids_oversized_concurrent_group():
    plan, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [
        mixed('CS300', concurrent('CS100'), condition('CS200')),
    ], target=3)
    assert terms['CS200'] < terms['CS300']
    assert all(s.total_credits <= 3 for s in plan.semesters)


@pytest.mark.asyncio
async def test_mixed_choice_retries_around_strict_cycle():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        mixed('CS100', flexible('CS200'), concurrent('CS300')),
        stored('CS200', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS100'] == terms['CS300'] < terms['CS200']


@pytest.mark.asyncio
async def test_fixed_corequisite_group_constrains_mixed_choice():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        coreq('CS100', 'CS200'), mixed('CS300', concurrent('CS100'), condition('CS400')),
    ], target=6)
    assert terms['CS100'] == terms['CS200']
    assert terms['CS400'] < terms['CS300']
    assert terms['CS300'] != terms['CS100']


@pytest.mark.asyncio
async def test_all_missing_paths_remain_partial_without_new_courses():
    plan, terms, _ = await generate(['CS300'], [mixed('CS300', concurrent('CS100'), flexible('CS200'))])
    assert set(terms) == {'CS300'}
    assert any(w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_unavoidable_cycle_remains_partial_and_terminates():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        mixed('CS100', flexible('CS200'), concurrent('CS300')),
        stored('CS200', prereq=condition('CS100')), stored('CS300', prereq=condition('CS100')),
    ], target=6)
    assert set(terms) == {'CS100', 'CS200', 'CS300'}
    assert any('mixed prerequisite alternative selection' in w and 'conflict' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_unsupported_branch_preserves_full_tree_uncertainty():
    row = mixed('CS200', concurrent('CS100'), UnresolvedCondition(reason='Permission required'))
    plan, terms, _ = await generate(['CS200', 'CS100'], [row], target=3)
    assert terms['CS200'] != terms['CS100']
    assert any('recorded condition is unresolved' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_mixed_choice_survives_capstone_phase_and_keeps_original_diagnostics():
    plan, terms, _ = await generate(['CS200', 'CS100'], [
        mixed('CS200', condition('CS300'), concurrent('CS100').model_copy(update={'required_crn': '12345'})),
    ], senior={'CS100'}, target=3)
    assert terms['CS200'] == terms['CS100']
    assert any('required section 12345' in w for w in plan.warnings)
    assert any('CS300' in w and ' OR ' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_choice_does_not_depend_on_row_or_branch_order():
    rows = [mixed('CS100', flexible('CS200'), concurrent('CS300')), stored('CS200', prereq=condition('CS100'))]
    _, first, _ = await generate(['CS100', 'CS200', 'CS300'], rows, target=6)
    rows[0]['prerequisites_rules']['prerequisites']['items'].reverse()
    _, second, _ = await generate(['CS100', 'CS200', 'CS300'], list(reversed(rows)), target=6)
    assert first == second


@pytest.mark.asyncio
async def test_missing_escape_does_not_prevent_backtracking_available_choices():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        mixed('CS100', condition('CS200'), condition('CS400')),
        mixed('CS200', flexible('CS100'), concurrent('CS300'), condition('CS500')),
        stored('CS300', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS400'] < terms['CS100'] <= terms['CS200']
    assert 'CS500' not in terms
    assert not any('CS500' in w and w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_oversized_fallback_keeps_only_chosen_concurrent_path():
    plan, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [
        mixed('CS300', concurrent('CS100'), concurrent('CS200')),
    ], target=3)
    assert terms['CS300'] == terms['CS100'] != terms['CS200']
    assert any('concurrent prerequisite group' in w and 'above your' in w for w in plan.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['MAX_TIMING_PATHS', 'MAX_TIMING_RULE_NODES'])
async def test_expansion_bounds_preserve_reviewable_fallback(monkeypatch, limit):
    from src.services import mixed_prerequisite_choices
    monkeypatch.setattr(mixed_prerequisite_choices, limit, 1)
    plan, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [
        mixed('CS300', concurrent('CS100'), flexible('CS200')),
    ], target=3)
    assert set(terms) == {'CS300', 'CS100', 'CS200'}
    assert any('mixed prerequisite alternatives' in w and 'expansion limit' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_search_bound_is_shared_across_target_and_oversized_passes(monkeypatch):
    from src.services import mixed_prerequisite_choices
    monkeypatch.setattr(mixed_prerequisite_choices, 'MAX_TIMING_SEARCH_STEPS', 2)
    plan, terms, _ = await generate(['CS300', 'CS100', 'CS200'], [
        mixed('CS300', concurrent('CS100'), concurrent('CS200')),
    ], target=3)
    assert set(terms) == {'CS300', 'CS100', 'CS200'}
    assert any('mixed prerequisite alternative selection' in w and 'search limit' in w for w in plan.warnings)
