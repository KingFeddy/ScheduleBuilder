"""Collect a complete term before making any database changes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from .banner import BANNER_BASE, BANNER_HOST, PAGE_SIZE, _fetch_page, _validate_results_page
from .prerequisites import fetch_subject_lookup


@dataclass(frozen=True)
class TermInventory:
    term: str
    started_at: datetime
    finished_at: datetime
    sections: list[dict]


async def discover_banner_subjects(term: str) -> list[str]:
    """Use Banner's subject codes verbatim, including cross-registration codes."""
    from types import SimpleNamespace
    async with async_playwright() as pw:
        request = await pw.request.new_context()
        try:
            lookup = await fetch_subject_lookup(SimpleNamespace(request=request), BANNER_BASE, term)
            return sorted(set(lookup.mapping.values()))
        finally:
            await request.dispose()


async def collect_term_inventory(term: str) -> TermInventory:
    """Fetch every page with no subject filter; reject incomplete/unstable snapshots."""
    records: list[dict] = []
    seen: set[str] = set()
    total = None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = await browser.new_page()
            await page.goto(f'{BANNER_BASE}/classSearch/classSearch', wait_until='networkidle', timeout=30_000)
            response = await page.request.post(f'{BANNER_BASE}/term/search?mode=search', form={
                'term': term, 'studyPath': '', 'studyPathText': '', 'startDatepicker': '',
                'endDatepicker': '', 'uniqueSessionId': f'inventory-{term}',
            })
            if response.status != 200:
                raise ValueError('Banner could not select the requested term.')
            forward = (await response.json()).get('fwdURL')
            if not isinstance(forward, str) or not forward.startswith('/StudentRegistrationSsb/'):
                raise ValueError('Unexpected Banner term-selection response.')
            await page.goto(BANNER_HOST + forward, wait_until='networkidle', timeout=30_000)
            started = datetime.now(timezone.utc)
            while total is None or len(records) < total:
                payload = await _fetch_page(page, f'{BANNER_BASE}/searchResults/searchResults', {
                    'txt_term': term, 'pageOffset': len(records), 'pageMaxSize': PAGE_SIZE,
                    'sortColumn': 'subjectDescription', 'sortDirection': 'asc',
                })
                rows, total = _validate_results_page(
                    payload, subject=None, term=term, offset=len(records), page_size=PAGE_SIZE,
                    expected_total=total, received_crns=seen,
                )
                if total == 0:
                    raise ValueError('An empty full-term result requires manual review; nothing will be changed.')
                records.extend(rows)
                seen.update(row['courseReferenceNumber'] for row in rows)
                if len(records) < total:
                    await asyncio.sleep(1)
            return TermInventory(term, started, datetime.now(timezone.utc), records)
        finally:
            await browser.close()
