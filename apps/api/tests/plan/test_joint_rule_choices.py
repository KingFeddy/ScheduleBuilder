"""Prerequisite and corequisite choices backtrack together without hiding evidence."""
import pytest

from src.schemas.prerequisites import AllConditions, AnyConditions
from tests.plan.test_corequisite_alternatives import choice, concurrent
from tests.plan.test_flexible_prerequisites import flexible
from tests.plan.test_mixed_timing_alternatives import mixed
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


@pytest.mark.asyncio
async def test_prior_alternative_retried_for_fixed_corequisite():
    row = mixed('CS100', condition('CS200'), condition('CS300'))
    row['prerequisites_rules']['corequisites'] = concurrent('CS200').model_dump()
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [row], target=6)
    assert terms['CS300'] < terms['CS100'] == terms['CS200']
    assert not any('grouping' in w and 'conflicts' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_corequisite_choice_accounts_for_fixed_flexible_dependency():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        stored('CS100', prereq=flexible('CS200')), choice('CS200', 'CS300', 'CS400'),
        stored('CS300', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS200'] == terms['CS400'] <= terms['CS100'] < terms['CS300']


@pytest.mark.asyncio
async def test_missing_corequisite_escape_does_not_prevent_joint_backtracking():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        mixed('CS100', concurrent('CS200'), concurrent('CS300')), choice('CS400', 'CS100', 'CS200', 'CS500'),
    ], target=6)
    assert terms['CS100'] == terms['CS300']
    assert terms['CS400'] == terms['CS200'] != terms['CS100']
    assert all(semester.total_credits <= 6 for semester in plan.semesters)
    assert 'CS500' not in terms
    assert not any(w.startswith('Partial plan:') and 'CS500' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_joint_choices_preserve_and_siblings_in_both_rule_trees():
    row = mixed('CS400', flexible('CS100'), condition('CS200'))
    row['prerequisites_rules']['corequisites'] = AllConditions(items=[
        concurrent('CS300'), AnyConditions(items=[concurrent('CS100'), concurrent('CS500')]),
    ]).model_dump()
    _, terms, _ = await generate(['CS400', 'CS300', 'CS100', 'CS200'], [row], target=9)
    assert terms['CS400'] == terms['CS300'] == terms['CS100']


@pytest.mark.asyncio
async def test_original_prerequisite_and_corequisite_alternatives_remain_in_diagnostics():
    row = mixed('CS100', concurrent('CS200'), concurrent('CS300'))
    other = choice('CS400', 'CS100', 'CS200')
    other['prerequisites_rules']['corequisites']['items'][1]['required_crn'] = '12345'
    plan, terms, session = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [row, other], target=6)
    assert terms['CS100'] == terms['CS300']
    assert terms['CS400'] == terms['CS200']
    assert any('CS100: prerequisite' in w and 'CS200' in w and 'CS300' in w and ' OR ' in w for w in plan.warnings)
    assert any('CS400: corequisite' in w and 'required section 12345' in w and ' OR ' in w for w in plan.warnings)
    assert sum('prerequisites_rules' in str(call.args[0]) for call in session.execute.call_args_list) == 1


@pytest.mark.asyncio
async def test_joint_choice_keeps_capstone_group_and_strict_descendant():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        mixed('CS100', condition('CS200'), condition('CS400')),
        choice('CS200', 'CS100', 'CS300'), stored('CS300', prereq=condition('CS100')),
    ], senior={'CS200'}, target=6)
    assert terms['CS400'] < terms['CS100'] == terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_no_compatible_combination_remains_partial_and_terminates():
    row = mixed('CS100', condition('CS200'), condition('CS300'))
    row['prerequisites_rules']['corequisites'] = AllConditions(items=[concurrent('CS200'), concurrent('CS300')]).model_dump()
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [row], target=9)
    assert set(terms) == {'CS100', 'CS200', 'CS300'}
    assert any('joint prerequisite/corequisite alternative selection' in w and 'conflict' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_joint_search_limit_preserves_all_selected_courses(monkeypatch):
    from src.services import mixed_prerequisite_choices
    monkeypatch.setattr(mixed_prerequisite_choices, 'MAX_TIMING_SEARCH_STEPS', 0)
    row = mixed('CS100', condition('CS200'), condition('CS300'))
    row['prerequisites_rules']['corequisites'] = concurrent('CS200').model_dump()
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [row], target=6)
    assert set(terms) == {'CS100', 'CS200', 'CS300'}
    assert any('joint prerequisite/corequisite alternative selection' in w and 'search limit' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_path_limit_is_per_tree_without_materializing_cross_product(monkeypatch):
    from src.services import mixed_prerequisite_choices
    monkeypatch.setattr(mixed_prerequisite_choices, 'MAX_TIMING_PATHS', 2)
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        mixed('CS100', concurrent('CS200'), concurrent('CS300')), choice('CS400', 'CS100', 'CS200'),
    ], target=6)
    assert terms['CS100'] == terms['CS300']
    assert terms['CS400'] == terms['CS200'] != terms['CS100']
    assert not any('expansion limit' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_both_selected_fields_on_same_course_survive_ordering_copy():
    row = mixed('CS100', condition('CS200'), condition('CS300'))
    row['prerequisites_rules']['corequisites'] = AnyConditions(items=[concurrent('CS200'), concurrent('CS400')]).model_dump()
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        row, stored('CS400', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS300'] < terms['CS100'] == terms['CS200'] < terms['CS400']


@pytest.mark.asyncio
async def test_joint_selection_is_independent_of_row_and_branch_order():
    rows = [mixed('CS100', condition('CS200'), condition('CS400')),
            choice('CS200', 'CS100', 'CS300'), stored('CS300', prereq=condition('CS100'))]
    _, first, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], rows, target=6)
    rows[0]['prerequisites_rules']['prerequisites']['items'].reverse()
    rows[1]['prerequisites_rules']['corequisites']['items'].reverse()
    _, second, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], list(reversed(rows)), target=6)
    assert first == second
