"""Concurrent alternatives choose whole compatible groups among selected courses."""
import pytest

from src.schemas.prerequisites import AllConditions, AnyConditions, UnresolvedCondition
from src.services import corequisite_groups as grouping
from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def concurrent(code):
    return condition(code, timing='concurrent')


def choice(code, *options):
    return stored(code, prereq=AllConditions(items=[]), coreq=AnyConditions(
        items=[concurrent(option) for option in options]))


@pytest.mark.asyncio
async def test_selects_available_corequisite_without_inventing_missing_alternative():
    plan, terms, session = await generate(['CS100', 'CS300'], [choice('CS100', 'CS200', 'CS300')], target=3)
    assert terms['CS100'] == terms['CS300']
    assert set(terms) == {'CS100', 'CS300'}
    assert not any('not selected' in warning for warning in plan.warnings)
    assert sum('prerequisites_rules' in str(call.args[0]) for call in session.execute.call_args_list) == 1


@pytest.mark.asyncio
async def test_nested_and_siblings_remain_required_without_taking_every_or_branch():
    rule = AllConditions(items=[concurrent('CS200'), AnyConditions(items=[concurrent('CS300'), concurrent('CS400')])])
    row = stored('CS100', prereq=AllConditions(items=[]), coreq=rule)
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [row], target=9)
    assert terms['CS100'] == terms['CS200'] == terms['CS300']
    assert terms['CS400'] != terms['CS100']
    assert sum(semester.total_credits for semester in plan.semesters) == 12


@pytest.mark.asyncio
async def test_retries_alternative_that_conflicts_with_prior_ordering():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        choice('CS100', 'CS200', 'CS300'), stored('CS200', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS100'] == terms['CS300'] < terms['CS200']


@pytest.mark.asyncio
async def test_transitive_credit_conflict_chooses_smaller_group():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        choice('CS100', 'CS200', 'CS300'), coreq('CS200', 'CS400'),
    ], target=6)
    assert terms['CS100'] == terms['CS300']
    assert terms['CS200'] == terms['CS400'] != terms['CS100']
    assert all(semester.total_credits <= 6 for semester in plan.semesters)
    assert not any('above your' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_backtracks_earlier_choice_when_later_groups_exceed_target():
    # First A-B forces C to join A/B through either option. A-D leaves C-B.
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        choice('CS100', 'CS200', 'CS400'), choice('CS300', 'CS100', 'CS200'),
    ], target=6)
    assert terms['CS100'] == terms['CS400']
    assert terms['CS300'] == terms['CS200'] != terms['CS100']


@pytest.mark.asyncio
async def test_missing_escape_does_not_hide_available_path_backtracking():
    rows = [choice('CS100', 'CS200', 'CS400'), choice('CS300', 'CS100', 'CS200', 'CS500')]
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], rows, target=6)
    assert terms['CS100'] == terms['CS400']
    assert terms['CS300'] == terms['CS200']
    assert 'CS500' not in terms
    assert not any('not selected' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_oversized_fallback_still_groups_courses_and_reports_target():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [choice('CS100', 'CS200', 'CS300')], target=3)
    assert terms['CS100'] == terms['CS200'] != terms['CS300']
    assert any('above your 3-credit target' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_all_missing_options_remain_explicitly_partial():
    plan, terms, _ = await generate(['CS100'], [choice('CS100', 'CS200', 'CS300')])
    assert set(terms) == {'CS100'}
    assert any(warning.startswith('Partial plan:') and 'not selected' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_all_choices_with_prior_conflicts_keep_separate_proposals():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [
        choice('CS100', 'CS200', 'CS300'), stored('CS200', prereq=condition('CS100')),
        stored('CS300', prereq=condition('CS100')),
    ], target=6)
    assert terms['CS100'] < terms['CS200'] and terms['CS100'] < terms['CS300']
    assert any('corequisite alternative selection' in warning and 'prior-course' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_unrelated_fixed_conflict_does_not_prevent_other_alternative_selection():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400', 'CS500'], [
        coreq('CS100', 'CS200'), stored('CS200', prereq=condition('CS100')),
        choice('CS300', 'CS400', 'CS500'), stored('CS400', prereq=condition('CS300')),
    ], target=6)
    assert terms['CS100'] < terms['CS200']
    assert terms['CS300'] == terms['CS500'] < terms['CS400']


@pytest.mark.asyncio
async def test_selected_alternative_stays_intact_despite_senior_label():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [choice('CS100', 'CS200', 'CS400')], senior={'CS200'}, target=6)
    assert terms['CS100'] == terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_unsupported_alternative_does_not_become_a_known_choice():
    row = stored('CS100', prereq=AllConditions(items=[]), coreq=AnyConditions(items=[
        concurrent('CS200'), UnresolvedCondition(reason='Permission required'),
    ]))
    plan, terms, _ = await generate(['CS100', 'CS200'], [row], target=3)
    assert terms['CS100'] != terms['CS200']
    assert any('recorded condition is unresolved' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_alternative_expansion_limit_is_explicit(monkeypatch):
    monkeypatch.setattr(grouping, 'MAX_COREQUISITE_ALTERNATIVES', 1)
    plan, _, _ = await generate(['CS100', 'CS200', 'CS300'], [choice('CS100', 'CS200', 'CS300')])
    assert any('corequisite alternatives for CS100' in warning and 'expansion limit' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_alternative_search_limit_is_explicit_and_keeps_all_courses(monkeypatch):
    monkeypatch.setattr(grouping, 'MAX_COREQUISITE_SEARCH_STEPS', 0)
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [choice('CS100', 'CS200', 'CS300')])
    assert set(terms) == {'CS100', 'CS200', 'CS300'}
    assert any('corequisite alternative selection' in warning and 'search limit' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_choice_is_independent_of_row_and_or_branch_order():
    rows = [choice('CS100', 'CS200', 'CS300'), coreq('CS200', 'CS400')]
    _, first, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], rows, target=6)
    rows[0]['prerequisites_rules']['corequisites']['items'].reverse()
    _, second, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], list(reversed(rows)), target=6)
    assert first == second


@pytest.mark.asyncio
async def test_backtracks_union_snapshots_for_cross_choice_prior_conflicts():
    second = choice('CS300', 'CS100', 'CS200')
    second['prerequisites_rules']['prerequisites'] = condition('CS100').model_dump()
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300', 'CS400'], [
        choice('CS100', 'CS200', 'CS400'), second,
    ], target=12)
    assert terms['CS100'] == terms['CS400'] < terms['CS200'] == terms['CS300']
    assert not any('corequisite grouping' in warning and 'conflicts' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_rule_node_limit_is_explicit(monkeypatch):
    monkeypatch.setattr(grouping, 'MAX_COREQUISITE_RULE_NODES', 1)
    plan, _, _ = await generate(['CS100', 'CS200', 'CS300'], [choice('CS100', 'CS200', 'CS300')])
    assert any('corequisite alternatives for CS100' in warning and 'expansion limit' in warning for warning in plan.warnings)
