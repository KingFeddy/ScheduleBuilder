"""Historical absence is evidence only when the entire comparison window is known."""
from unittest.mock import AsyncMock

import pytest

from src.schemas.plan import StillNeededItem, ParseValidationError, ParsedDegreeValidated, PlanPreferences
from src.services import offering_history
from src.services.course_offerings import common_offering_seasons, unavailable_offering_reason
from src.services.offering_history import classify_history
from src.services.plan import select_best_option, generate_plan
from tests.plan.test_structured_ordering import generate


def snapshots():
    rows = []
    for year in range(2022, 2027):
        for season in ('10', '50', '90'):
            term = f'{year}{season}'
            if not '202290' <= term <= '202690':
                continue
            codes = ['CS100']
            if season == '10':
                codes += ['PHYS200']
            if season == '90':
                codes += ['PHYS300']
            if season == '50':
                codes += ['CS200']
            if term == '202290':
                codes += ['CS400']
            if term == '202690':
                codes += ['CS500']
            rows.append({'format': 1, 'term': term, 'complete': True,
                         'finished_at': '2026-09-28T00:00:00+00:00',
                         'real_sections': len(codes), 'total_sections': len(codes),
                         'courses': {code: {'sections': 1, 'title': code} for code in codes}})
    return rows


def classify(rows):
    return classify_history(rows, baseline_start='202290', recent_start='202390', latest_term='202690')


def test_recent_patterns_distinguish_inactivity_summer_and_insufficient_evidence():
    history = classify(snapshots())
    assert {code: row['category'] for code, row in history['courses'].items()} == {
        'CS100': 'fall_and_spring', 'CS200': 'summer_only', 'CS400': 'inactive',
        'CS500': 'insufficient_pattern', 'PHYS200': 'spring_only', 'PHYS300': 'fall_only',
    }
    assert history['courses']['CS400']['last_offered'] == '202290'
    assert history['courses']['CS500']['planning_seasons'] == ['10', '90']


def test_missing_failed_duplicate_or_partial_inventory_cannot_imply_absence():
    original = snapshots()
    invalid = [original[:-1], original + [original[0]],
               [dict(row, complete=False) if index == 0 else row for index, row in enumerate(original)],
               [dict(row, real_sections=999) if index == 0 else row for index, row in enumerate(original)]]
    for rows in invalid:
        with pytest.raises(ValueError):
            classify(rows)
    with pytest.raises(ValueError, match='three years'):
        classify_history(original, baseline_start='202290', recent_start='202490', latest_term='202690')


def install(monkeypatch):
    history = classify(snapshots())
    monkeypatch.setattr(offering_history, 'load_offering_history', lambda: history)
    return history


def test_override_unknown_and_past_plans_are_preserved(monkeypatch):
    history = install(monkeypatch)
    history['courses']['PHYS485'] = history['courses']['CS400']
    assert common_offering_seasons(['PHYS485'], '202690') == {'10'}
    assert unavailable_offering_reason('PHYS485', '202690') is None
    assert common_offering_seasons(['PHYS200'], '202590') == {'10', '90'}
    assert unavailable_offering_reason('CS400', '202590') is None
    assert common_offering_seasons(['ZZ100'], '202690') == {'10', '90'}


@pytest.mark.asyncio
async def test_history_drives_placement_and_preserves_unavailable_required_slot(monkeypatch):
    install(monkeypatch)
    plan, terms, _ = await generate(['CS400', 'PHYS200', 'PHYS300'], [], target=9)
    assert 'CS400' not in terms
    assert terms['PHYS200'] == '202710'
    assert terms['PHYS300'] == '202690'
    unresolved = [c for semester in plan.semesters for c in semester.courses if c.course_code == 'TBD']
    assert len(unresolved) == 1
    assert unresolved[0].badge == 'TBD'
    assert 'CS400 has not appeared' in unresolved[0].catalog_note
    assert any('CS400 has not appeared' in warning for warning in plan.warnings)


@pytest.mark.asyncio
async def test_inactive_and_summer_options_do_not_block_an_available_alternative(monkeypatch):
    install(monkeypatch)
    session = AsyncMock()
    session.execute.return_value.mappings = lambda: []
    item = StillNeededItem(requirement='Elective', options=['CS400', 'CS200', 'CS100'])
    assert await select_best_option(item, set(), set(), [], '202690', session) == ('CS100', 1)


@pytest.mark.asyncio
async def test_explicit_unavailable_elective_is_rejected_before_database_access(monkeypatch):
    install(monkeypatch)
    session = AsyncMock()
    with pytest.raises(ParseValidationError, match='CS400 has not appeared'):
        await generate_plan(ParsedDegreeValidated(majors=['Synthetic']),
                            PlanPreferences(courses=['CS400'], credits_per_semester=15, start_term='202690'), session)
    session.execute.assert_not_called()
