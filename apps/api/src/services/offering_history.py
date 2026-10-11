"""Classify complete historical schedules; missing snapshots never imply absence."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parents[1] / 'data' / 'course_offering_history.json'


def classify_history(snapshots: list[dict], *, baseline_start: str, recent_start: str, latest_term: str) -> dict:
    bounds = (baseline_start, recent_start, latest_term)
    if any(len(term) != 6 or not term.isdigit() or term[-2:] not in {'10', '50', '90'} for term in bounds):
        raise ValueError('Invalid historical term boundary.')
    if (int(latest_term[:4]) - int(recent_start[:4]), latest_term[-2:]) < (3, recent_start[-2:]):
        raise ValueError('Inactivity needs at least three years of complete recent history.')
    expected = {f'{year}{season}' for year in range(int(baseline_start[:4]), int(latest_term[:4]) + 1)
                for season in ('10', '50', '90') if baseline_start <= f'{year}{season}' <= latest_term}
    by_term = {snapshot['term']: snapshot for snapshot in snapshots}
    if len(by_term) != len(snapshots) or set(by_term) != expected:
        raise ValueError('Every fall, spring, and summer in the requested window must have exactly one complete snapshot.')
    if not baseline_start < recent_start < latest_term:
        raise ValueError('History must include an older baseline and a recent comparison window.')
    for term, snapshot in by_term.items():
        courses = snapshot['courses']
        if (snapshot.get('format') != 1 or snapshot.get('complete') is not True or not courses
                or snapshot['real_sections'] <= 0 or snapshot['total_sections'] < snapshot['real_sections']
                or sum(row['sections'] for row in courses.values()) != snapshot['real_sections']
                or any(type(row['sections']) is not int or row['sections'] <= 0 for row in courses.values())):
            raise ValueError(f'Incomplete or inconsistent snapshot: {term}')
    observed: dict[str, list[str]] = {}
    titles = {}
    for term in sorted(by_term):
        for code, row in by_term[term]['courses'].items():
            observed.setdefault(code, []).append(term)
            titles[code] = row['title']
    courses = {}
    for code, terms in sorted(observed.items()):
        recent = [term for term in terms if term >= recent_start]
        counts = Counter(term[-2:] for term in recent)
        if not recent:
            category, seasons = 'inactive', []
        elif counts['10'] >= 2 and not counts['90']:
            category, seasons = ('spring_and_summer' if counts['50'] else 'spring_only'), ['10']
        elif counts['90'] >= 2 and not counts['10']:
            category, seasons = ('fall_and_summer' if counts['50'] else 'fall_only'), ['90']
        elif counts['50'] >= 2 and not (counts['10'] or counts['90']):
            category, seasons = 'summer_only', []
        elif counts['10'] and counts['90']:
            category, seasons = 'fall_and_spring', ['10', '90']
        else:
            category, seasons = 'insufficient_pattern', ['10', '90']
        courses[code] = {'title': titles[code], 'observed_terms': terms, 'last_offered': terms[-1],
                         'category': category, 'planning_seasons': seasons}
    return {'format': 1, 'source': 'NJIT Banner complete public section inventories',
            'baseline_start': baseline_start, 'recent_start': recent_start, 'latest_term': latest_term,
            'minimum_season_observations': 2,
            'terms': [{key: by_term[term][key] for key in ('term', 'finished_at', 'total_sections', 'real_sections')}
                      for term in sorted(by_term)], 'courses': courses}


@lru_cache(maxsize=1)
def load_offering_history() -> dict:
    if not HISTORY_PATH.exists():
        return {}
    return json.loads(HISTORY_PATH.read_text())


def course_history(code: str | None, term: str | None = None) -> dict | None:
    history = load_offering_history()
    if term and term < history.get('latest_term', ''):
        return None  # Do not project today's inferred inactivity into a past plan.
    return history.get('courses', {}).get(code)
