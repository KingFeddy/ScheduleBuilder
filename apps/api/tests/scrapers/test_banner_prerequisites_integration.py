"""
Integration test proving scrape_subject's prerequisite-fetching actually
writes to courses.prerequisites — real Postgres, mocked Playwright network
(reusing test_banner_resilience.py's proven mock helper), not a live
Banner call.
"""
from __future__ import annotations
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from tests.scrapers.test_banner_resilience import (
    _mock_playwright_returning,
    _FAKE_SUBJECT,
    _assert_fake_subject_is_actually_empty,
)


@pytest.mark.asyncio
async def test_prerequisites_written_to_courses_table(db_session):
    from src.scrapers import banner as banner_module

    await _assert_fake_subject_is_actually_empty(db_session)
    # _assert_fake_subject_is_actually_empty's SELECT auto-begins a session
    # transaction that's otherwise never closed; scrape_subject's own
    # `async with session.begin()` calls (in _upsert_section_with_meetings
    # and _delete_stale_sections) require a transaction-free session to
    # start from, exactly like the other tests reusing this same assert
    # helper (which happen to close it via their own seed-data commit).
    await db_session.commit()

    # One section for a fake course under the reserved ZZZ prefix.
    # scrape_subject's existing section-upsert path auto-creates the
    # courses stub row (course_code, title, credits) before this test's
    # new prerequisite-fetch code ever runs — no need to pre-seed it here.
    raw_section = {
        "courseReferenceNumber": "77777",
        "subject": _FAKE_SUBJECT,
        "courseNumber": "997",
        "seatsAvailable": 10,
        "maximumEnrollment": 30,
        "meetingsFaculty": [],
    }

    mock_pw_cm = _mock_playwright_returning([raw_section])
    mock_page = (
        mock_pw_cm.__aenter__.return_value
        .chromium.launch.return_value
        .new_context.return_value
        .new_page.return_value
    )

    subject_lookup_json = json.dumps(
        [{"code": _FAKE_SUBJECT, "description": "Fake Test Subject"}]
    )
    prereq_html = """
        <table class="basePreqTable"><tbody>
            <tr><td></td><td></td><td></td><td></td>
                <td>Fake Test Subject</td><td>996</td><td>Undergraduate</td><td>C</td><td></td></tr>
        </tbody></table>
    """

    async def fake_post(url, **kwargs):
        if "term/search" in url:
            resp = AsyncMock()
            resp.status = 200
            resp.text = AsyncMock(return_value='{"fwdURL": ""}')
            return resp
        if "getSectionPrerequisites" in url:
            resp = AsyncMock()
            resp.text = AsyncMock(return_value=prereq_html)
            return resp
        raise AssertionError(f"Unexpected POST to {url}")

    async def fake_get(url, **kwargs):
        if "get_subject" in url:
            resp = AsyncMock()
            resp.text = AsyncMock(return_value=subject_lookup_json)
            return resp
        raise AssertionError(f"Unexpected GET to {url}")

    mock_page.request.post = AsyncMock(side_effect=fake_post)
    mock_page.request.get = AsyncMock(side_effect=fake_get)

    async def fake_fetch_page(page, url, params, timeout_ms=30_000):
        return {"data": [raw_section], "totalCount": 1}

    with patch("src.scrapers.banner.async_playwright", return_value=mock_pw_cm):
        with patch("src.scrapers.banner._fetch_page", side_effect=fake_fetch_page):
            upserted, failed, deleted = await banner_module.scrape_subject(
                db_session, _FAKE_SUBJECT, "202690"
            )

    assert upserted == 1
    assert failed == 0

    result = await db_session.execute(
        text("SELECT prerequisites FROM courses WHERE course_code = 'ZZZ997'")
    )
    row = result.first()
    assert row is not None
    assert row.prerequisites == ["ZZZ996"]
