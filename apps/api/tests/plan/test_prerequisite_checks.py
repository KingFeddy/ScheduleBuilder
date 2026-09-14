"""Synthetic rule and attempt evidence; no university eligibility assumptions."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import CourseAttempt, ParsedDegreeValidated, PlanPreferences, StillNeededItem
from src.schemas.prerequisites import AllConditions, AnyConditions, CourseCondition, UnresolvedCondition
from src.services.prerequisite_checks import evaluate_rule, check_plan_prerequisites


def condition(code='CS100', **kwargs):
    return CourseCondition(course_code=code, minimum_grade=None, level=None,
                           timing=kwargs.pop('timing', 'prior'), source_row=1, **kwargs)


def audit(grade='B', term='Fall 2025', **kwargs):
    return ParsedDegreeValidated(majors=['Synthetic'], course_attempts=[
        CourseAttempt(course_code='CS100', grade=grade, term=term, credits=3),
    ], **kwargs)


def evaluate(rule, history=None, planned=None):
    return evaluate_rule(rule, history or audit(), planned or {}, '202690')


@pytest.mark.parametrize('grade,expected', [('A','satisfied'),('B+','satisfied'),('C','satisfied'),('D','unmet'),('F','unmet'),('P','unknown'),('TR','unknown'),('A-','unknown'),('IP','unknown')])
def test_minimum_grade_is_not_the_same_as_earned_credit(grade, expected):
    rule=condition().model_copy(update={'minimum_grade':'C'})
    assert evaluate(rule, audit(grade)).status == expected


def test_any_does_not_require_unused_alternative_and_all_does():
    met, absent = condition(), condition('CS101')
    assert evaluate(AnyConditions(items=[met, absent])).status == 'satisfied'
    assert evaluate(AllConditions(items=[met, absent])).status == 'unmet'
    unknown=UnresolvedCondition(reason='unrecognized source')
    assert evaluate(AnyConditions(items=[unknown, absent])).status == 'unknown'
    assert evaluate(AllConditions(items=[unknown, absent])).status == 'unmet'
    assert evaluate(AnyConditions(items=[met, unknown])).status == 'satisfied'
    assert evaluate(AllConditions(items=[])).status == 'satisfied'


@pytest.mark.parametrize('timing,term,expected', [('prior','202690','unmet'),('prior','202610','unknown'),('prior_or_concurrent','202690','unknown'),('concurrent','202610','unmet'),('concurrent','202690','unknown'),('unspecified','202610','unknown')])
def test_planned_course_timing_and_future_completion_are_separate(timing, term, expected):
    history=ParsedDegreeValidated(majors=['Synthetic'])
    result=evaluate(condition(timing=timing), history, {'CS100':term})
    assert result.status == expected


def test_missing_or_future_history_term_does_not_satisfy_prior_course():
    assert evaluate(condition(), audit(term=None)).status == 'unknown'
    assert evaluate(condition(), audit(term='Fall 2027')).status == 'unmet'
    assert evaluate(condition(), audit(term='202610')).status == 'satisfied'
    assert evaluate(condition(), ParsedDegreeValidated(majors=['Synthetic'], completed_courses=['CS100'])).status == 'unknown'


def test_later_retakes_do_not_erase_earlier_qualifying_attempt():
    history=audit('D')
    history=history.model_copy(update={'course_attempts': history.course_attempts + [CourseAttempt(course_code='CS100',grade='B',credits=3,term='Spring 2026')]})
    assert evaluate(condition().model_copy(update={'minimum_grade':'C'}), history).status == 'satisfied'


@pytest.mark.parametrize('field,value', [('level','Undergraduate'),('required_crn','12345'),('timing','unspecified')])
def test_unavailable_level_section_or_timing_stays_unknown(field, value):
    assert evaluate(condition().model_copy(update={field:value})).status == 'unknown'


def stored(code='CS200', status='verified', term='202690', prereq=None, coreq=None):
    return {'course_code':code,'prerequisites_status':status,'prerequisites_rules':{
        'schema_version':1,'scope':{'term':term,'crn':'12345'},
        'prerequisites':(prereq or condition()).model_dump(),
        'corequisites':(coreq or AllConditions(items=[])).model_dump(),
    }}


async def check(rows, history=None, terms=None):
    result=MagicMock()
    result.mappings.return_value=rows
    session=AsyncMock()
    session.execute.return_value=result
    warnings=await check_plan_prerequisites(session, history or audit(), terms or {'CS200':'202690'})
    return warnings,session


@pytest.mark.asyncio
async def test_check_uses_one_batched_query_and_reports_low_grade():
    warnings, session=await check([stored()], audit('D'))
    # No threshold in default fixture: D passes this rule; explicit C does not.
    assert not any('conflict' in w.lower() for w in warnings)
    warnings,session=await check([stored(prereq=condition().model_copy(update={'minimum_grade':'C'}))],audit('D'))
    assert any('CS200' in w and 'C' in w and 'conflict' in w.lower() for w in warnings)
    assert session.execute.await_count == 1
    assert session.execute.call_args.args[1] == {'codes':['CS200']}


@pytest.mark.asyncio
async def test_missing_failed_and_malformed_rules_are_not_empty_prerequisites():
    warnings,_=await check([stored(status='failed'), {'course_code':'CS300','prerequisites_status':'verified','prerequisites_rules':{}}],terms={'CS200':'202690','CS300':'202690','CS400':'202690'})
    assert any(all(code in w for code in ['CS200','CS300','CS400']) and 'unavailable' in w for w in warnings)


@pytest.mark.asyncio
async def test_different_term_scope_does_not_confirm_current_rule_applicability():
    warnings,_=await check([stored(term='202510')])
    assert any('CS200' in w and 'different term' in w for w in warnings)


@pytest.mark.asyncio
async def test_verified_empty_and_corequisite_conflict_are_distinct():
    empty=AllConditions(items=[])
    warnings,_=await check([stored(status='verified_empty',prereq=empty)])
    assert warnings == []
    warnings,_=await check([stored(prereq=empty,coreq=condition('CS101',timing='concurrent'))])
    assert any('corequisite' in w and 'CS101' in w for w in warnings)


@pytest.mark.asyncio
async def test_generation_includes_rule_diagnostics_without_losing_plan():
    from src.services.plan import generate_plan
    requirement=StillNeededItem(requirement='Synthetic course',options=['CS200'],remaining_quantity=1,quantity_unit='classes')
    history=audit('D',still_needed=[requirement])
    def execute(statement, params):
        result=MagicMock()
        if 'prerequisites_rules' in str(statement):
            result.mappings.return_value=[stored(prereq=condition().model_copy(update={'minimum_grade':'C'}))]
        elif 'FROM courses' in str(statement):
            result.mappings.return_value=[{'course_code':'CS200','title':'Synthetic','credits':3,'prerequisites':[]}]
        else:result.mappings.return_value=[]
        return result
    session=AsyncMock()
    session.execute.side_effect=execute
    generated=await generate_plan(history,PlanPreferences(courses=[],start_term='202690'),session)
    assert generated.semesters[0].courses[0].course_code == 'CS200'
    assert any('CS200' in w and 'conflict' in w.lower() for w in generated.warnings)


@pytest.mark.parametrize('credits', [0, None])
def test_successful_course_attempt_does_not_require_degree_credit_amount(credits):
    history=ParsedDegreeValidated(majors=['Synthetic'],course_attempts=[
        CourseAttempt(course_code='CS100',grade='B',credits=credits,term='Spring 2026'),
    ])
    assert evaluate(condition().model_copy(update={'minimum_grade':'C'}),history).status == 'satisfied'


@pytest.mark.asyncio
async def test_no_selected_courses_does_not_query_rules():
    session=AsyncMock()
    assert await check_plan_prerequisites(session,audit(),{}) == []
    session.execute.assert_not_called()
