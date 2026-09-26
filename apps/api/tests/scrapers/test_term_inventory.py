"""A complete term import must reject truncated, repeated, or wrong-term rows."""
import json
from datetime import datetime, timezone

import pytest

from scripts.reconcile_term_sections import load_inventory
from src.scrapers.banner import BannerResponseError


def snapshot(tmp_path, rows, total=None):
    path = tmp_path / 'inventory.json'
    now = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps({'term': '202690', 'total': len(rows) if total is None else total,
                               'started_at': now, 'finished_at': now, 'sections': rows}))
    return path


def section(crn, subject, term='202690'):
    return {'courseReferenceNumber': crn, 'subject': subject, 'courseNumber': '101',
            'term': term, 'meetingsFaculty': []}


def test_complete_inventory_accepts_mixed_and_cross_registered_subjects(tmp_path):
    rows = [section('1', 'CS'), section('2', 'R120')]
    assert load_inventory(snapshot(tmp_path, rows), '202690').sections == rows


@pytest.mark.parametrize('rows,total', [
    ([section('1', 'CS')], 2),
    ([section('1', 'CS'), section('1', 'R120')], 2),
    ([section('1', 'CS', term='202710')], 1),
    ([], 0),
])
def test_incomplete_or_inconsistent_inventory_is_rejected(tmp_path, rows, total):
    with pytest.raises((ValueError, BannerResponseError)):
        load_inventory(snapshot(tmp_path, rows, total), '202690')
