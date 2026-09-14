"""Requirement labels do not invent semester restrictions."""
import pytest

from src.schemas.prerequisites import AllConditions

from tests.plan.test_corequisite_groups import coreq
from tests.plan.test_prerequisite_checks import condition, stored
from tests.plan.test_structured_ordering import generate


@pytest.mark.asyncio
async def test_senior_labels_do_not_delay_verified_empty_courses():
    codes = ['CS100', 'CS200', 'CS300', 'CS400']
    rows = [stored(code, status='verified_empty', prereq=AllConditions(items=[])) for code in codes]
    _, plain, _ = await generate(codes, rows, target=6)
    _, senior, _ = await generate(codes, rows, senior={'CS100'}, target=6)
    assert senior == plain
    assert senior['CS100'] == '202690'


@pytest.mark.asyncio
async def test_legacy_dependency_on_senior_course_is_preserved():
    _, terms, _ = await generate(['CS300', 'CS200', 'CS100'], [],
                                 legacy={'CS300': ['CS200'], 'CS200': ['CS100']}, senior={'CS100'}, target=6)
    assert terms['CS100'] < terms['CS200'] < terms['CS300']


@pytest.mark.asyncio
async def test_independent_course_does_not_delay_entire_capstone_chain():
    rows = [stored('CS491', prereq=condition('CS490'))]
    _, terms, _ = await generate(['CS490', 'CS491', 'CS100', 'CS200'], rows,
                                 senior={'CS490', 'CS491'}, target=6)
    assert terms['CS490'] == '202690'
    assert terms['CS491'] == '202710'
    assert terms['CS490'] < terms['CS491']


@pytest.mark.asyncio
async def test_same_term_group_is_not_postponed_by_member_label():
    _, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [coreq('CS100', 'CS200')],
                                 senior={'CS200'}, target=6)
    assert terms['CS100'] == terms['CS200'] == '202690'
    assert terms['CS300'] > terms['CS100']


@pytest.mark.asyncio
async def test_senior_label_does_not_replace_missing_rule_evidence():
    plan, terms, _ = await generate(['CS100', 'CS200'], [], senior={'CS100'}, target=3)
    assert terms['CS100'] == '202690'
    assert any('unavailable or unverified' in warning and 'CS100' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_label_change_preserves_course_credits_and_allocation():
    plan, terms, _ = await generate(['CS100', 'CS200', 'CS300'], [stored('CS300', prereq=condition('CS100'))],
                                   senior={'CS100', 'CS300'}, target=6)
    assert terms['CS100'] < terms['CS300']
    courses = [course for semester in plan.semesters for course in semester.courses]
    assert sum(semester.total_credits for semester in plan.semesters) == 9
    assert len({course.slot_id for course in courses}) == 3
    assert all(course.allocation is not None and course.requirement is not None for course in courses)
