"""Only actual sections count as positive historical offering evidence."""
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock

import pytest

from scripts import collect_offering_history
from src.scrapers.term_inventory import TermInventory
from playwright.async_api import Error as PlaywrightError


@pytest.mark.asyncio
async def test_placeholder_sections_are_excluded_and_completed_snapshot_is_reusable(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    collect = AsyncMock(return_value=TermInventory('202690', now, now, [
        {'subject': 'CS', 'courseNumber': '100', 'sequenceNumber': '001', 'courseTitle': 'Computing'},
        {'subject': 'CS', 'courseNumber': '100', 'sequenceNumber': '002', 'courseTitle': 'Computing'},
        {'subject': 'CS', 'courseNumber': '200', 'sequenceNumber': 'FP', 'courseTitle': 'Placeholder'},
    ]))
    monkeypatch.setattr(collect_offering_history, 'collect_term_inventory', collect)
    await collect_offering_history.run(['202690'], tmp_path)
    result = json.loads((tmp_path / '202690.json').read_text())
    assert result['complete'] is True
    assert result['total_sections'] == 3 and result['real_sections'] == 2
    assert result['courses'] == {'CS100': {'sections': 2, 'title': 'Computing'}}
    await collect_offering_history.run(['202690'], tmp_path)
    collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupted_inventory_is_never_saved_as_absent_courses(monkeypatch, tmp_path):
    collect = AsyncMock(side_effect=PlaywrightError('ERR_NETWORK_IO_SUSPENDED'))
    monkeypatch.setattr(collect_offering_history, 'collect_term_inventory', collect)
    monkeypatch.setattr(collect_offering_history.asyncio, 'sleep', AsyncMock())
    with pytest.raises(PlaywrightError):
        await collect_offering_history.run(['202690'], tmp_path)
    assert collect.await_count == 3
    assert not list(tmp_path.iterdir())
