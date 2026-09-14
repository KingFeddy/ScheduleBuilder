"""Choose supported OR paths without requiring every alternative or inventing courses."""
import pytest

from src.schemas.plan import CourseAttempt, ParsedDegreeValidated
from src.schemas.prerequisites import AllConditions, AnyConditions
from src.services import prerequisite_checks as checks
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


def either(*codes):
    return AnyConditions(items=[condition(code) for code in codes])


@pytest.mark.asyncio
async def test_selects_available_alternative_instead_of_missing_first_choice():
    plan,terms,_=await generate(['CS300','CS200'],[stored('CS300',prereq=either('CS100','CS200'))])
    assert terms['CS200'] < terms['CS300']
    assert not any(w.startswith('Partial plan: prerequisite ordering') for w in plan.warnings)
    assert 'CS100' not in terms


@pytest.mark.asyncio
async def test_completed_qualifying_alternative_clears_stale_legacy_dependencies():
    plan,terms,_=await generate(['CS300','CS200'],[stored('CS300',prereq=either('CS100','CS200'))],
                              legacy={'CS300':['CS200']},attempts=[CourseAttempt(course_code='CS100',grade='B',term='Spring 2026',credits=3)])
    assert terms['CS300'] == terms['CS200']
    assert not any('CS300: prerequisite' in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_nested_and_or_keeps_required_sibling_and_selects_only_one_branch():
    rule=AllConditions(items=[condition('CS100'),either('CS200','CS400')])
    _,terms,_=await generate(['CS300','CS100','CS200'],[stored('CS300',prereq=rule)],target=3)
    assert terms['CS100'] < terms['CS300'] and terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_low_grade_alternative_does_not_override_valid_planned_path():
    rule=AnyConditions(items=[condition('CS100').model_copy(update={'minimum_grade':'C'}),condition('CS200')])
    _,terms,_=await generate(['CS300','CS200'],[stored('CS300',prereq=rule)],attempts=[
        CourseAttempt(course_code='CS100',grade='D',term='Spring 2026',credits=3)])
    assert terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_skips_alternative_that_creates_cycle_with_fixed_chain():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[
        stored('CS100',prereq=either('CS200','CS300')),stored('CS200',prereq=condition('CS100'))])
    assert terms['CS300'] < terms['CS100'] < terms['CS200']
    assert not any(w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_choice_backtracking_finds_compatible_path_across_multiple_or_rules():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[
        stored('CS100',prereq=either('CS200','CS300')),
        stored('CS200',prereq=AnyConditions(items=[condition('CS100'),AllConditions(items=[condition('CS100'),condition('CS300')])]))])
    assert terms['CS300'] < terms['CS100'] < terms['CS200']
    assert not any(w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_all_missing_alternatives_remain_partial_without_added_courses():
    plan,terms,_=await generate(['CS300'],[stored('CS300',prereq=either('CS100','CS200'))])
    assert set(terms) == {'CS300'}
    assert any(w.startswith('Partial plan: prerequisite ordering') for w in plan.warnings)


@pytest.mark.asyncio
async def test_unavoidable_choice_cycle_is_reported_without_hanging():
    plan,terms,_=await generate(['CS100','CS200'],[
        stored('CS100',prereq=either('CS100','CS200')),stored('CS200',prereq=condition('CS100'))])
    assert set(terms) == {'CS100','CS200'}
    assert any('alternative' in w and w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_alternative_dependency_crossing_capstone_boundary_is_preserved():
    _,terms,_=await generate(['CS300','CS200'],[stored('CS300',prereq=either('CS100','CS200'))],senior={'CS200'})
    assert terms['CS200'] == '202690' and terms['CS300'] > terms['CS200']


@pytest.mark.asyncio
async def test_expansion_limit_keeps_explicit_partial_notice(monkeypatch):
    monkeypatch.setattr(checks,'MAX_PRIOR_ALTERNATIVES',2)
    rule=AllConditions(items=[either('CS100','CS200'),either('CS400','CS500')])
    plan,_,_=await generate(['CS300','CS100','CS400'],[stored('CS300',prereq=rule)])
    assert any('alternative' in w and 'limit' in w and w.startswith('Partial plan:') for w in plan.warnings)


@pytest.mark.asyncio
async def test_search_limit_is_distinct_from_impossible_rules(monkeypatch):
    monkeypatch.setattr(checks,'MAX_PRIOR_SEARCH_STEPS',0)
    plan,_,_=await generate(['CS300','CS100','CS200'],[stored('CS300',prereq=either('CS100','CS200'))])
    assert any('search limit' in w and w.startswith('Partial plan:') for w in plan.warnings)


def test_choices_do_not_depend_on_dictionary_insertion_order():
    rows={code:stored(code,prereq=either('CS100','CS200')) for code in ['CS300','CS400']}
    legacy={'CS300':[],'CS400':[],'CS100':[],'CS200':[]}
    audit=ParsedDegreeValidated(majors=['Synthetic'])
    a=checks.prior_course_ordering(rows,audit,legacy,'202690')
    b=checks.prior_course_ordering(rows,audit,dict(reversed(list(legacy.items()))),'202690')
    assert a == b


@pytest.mark.asyncio
async def test_missing_escape_branch_does_not_prevent_backtracking_to_complete_path():
    plan,terms,_=await generate(['CS100','CS200','CS300'],[
        stored('CS100',prereq=either('CS200','CS300')),
        stored('CS200',prereq=either('CS100','CS400'))])
    assert terms['CS300'] < terms['CS100'] < terms['CS200']
    assert not any(w.startswith('Partial plan:') for w in plan.warnings)
