"""Scraper retry, persistence, and failure recovery regressions."""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── HTTP response handling ───────────────────────────────────────────────────


def test_blocked_error_is_not_retried():
    """
    BannerBlockedError must propagate immediately out of scrape_subject.
    Retrying a blocked IP returns the same 403 — no point retrying.
    """
    from src.scrapers.banner import scrape_subject, BannerBlockedError

    async def run():
        mock_session = AsyncMock()

        # Reuse the shared helper (defined below in this file) so the mocked
        # term-selection POST actually returns status=200 — a hand-rolled
        # bare AsyncMock() here means term_resp.status != 200 always holds,
        # which makes scrape_subject raise BannerBlockedError from its own
        # term-selection check before _fetch_page is ever reached, silently
        # testing the wrong code path (this exact bug was live and failing
        # in CI before being caught).
        mock_pw_cm = _mock_playwright_returning([])

        with patch("src.scrapers.banner.async_playwright", return_value=mock_pw_cm):
            with patch(
                "src.scrapers.banner._fetch_page",
                side_effect=BannerBlockedError("403"),
            ) as mock_fetch:
                with pytest.raises(BannerBlockedError):
                    await scrape_subject(mock_session, "CS", "202690")

                assert mock_fetch.call_count == 1, "Must not retry after a block"

    asyncio.run(run())


# ─── Negative open_seats clamping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_negative_open_seats_clamped_to_zero(db_session):
    """
    Banner occasionally returns negative open_seats for waitlisted courses.
    These must be clamped to 0 before storage.
    """
    from src.scrapers.banner import _upsert_section_with_meetings

    raw_section = {
        "courseReferenceNumber": "99999",
        "subject": "CS",
        "courseNumber": "999",
        "seatsAvailable": -3,
        "maximumEnrollment": 30,
        "meetingsFaculty": [],
    }

    await _upsert_section_with_meetings(db_session, raw_section, "202690")

    from sqlalchemy import text
    result = await db_session.execute(
        text("SELECT open_seats FROM sections WHERE crn = '99999' AND term = '202690'")
    )
    row = result.mappings().first()
    assert row["open_seats"] == 0, "Negative open_seats must be clamped to 0"


# ─── Uncatalogued courses ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uncatalogued_course_gets_stub_row_before_section_insert(db_session):
    """
    Banner returns sections for course codes that may not exist in the courses
    table yet (e.g. newly-added special-topics numbers). A stub courses row
    must be created first so the sections FK doesn't silently drop the section.
    """
    from src.scrapers.banner import _upsert_section_with_meetings

    raw_section = {
        "courseReferenceNumber": "88888",
        "subject": "CS",
        "courseNumber": "998",
        "seatsAvailable": 5,
        "maximumEnrollment": 10,
        "meetingsFaculty": [],
    }

    await _upsert_section_with_meetings(db_session, raw_section, "202690")

    from sqlalchemy import text
    course_row = await db_session.execute(
        text("SELECT title, credits FROM courses WHERE course_code = 'CS998'")
    )
    course = course_row.mappings().first()
    assert course is not None, "Stub courses row must be created for an uncatalogued course"

    section_row = await db_session.execute(
        text("SELECT crn FROM sections WHERE crn = '88888' AND term = '202690'")
    )
    assert section_row.mappings().first() is not None, "Section must be inserted, not silently dropped"


# ─── Stale section cleanup ────────────────────────────────────────────────────

def _mock_playwright_returning(sections: list[dict], total: int | None = None):
    """
    Build a mocked async_playwright context manager whose page navigates
    through term-selection successfully, then _fetch_page (patched
    separately by the caller) is the only thing that needs configuring for
    the actual section data.
    """
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()

    mock_term_resp = AsyncMock()
    mock_term_resp.status = 200
    mock_term_resp.text = AsyncMock(return_value='{"fwdURL": ""}')
    mock_page.request.post = AsyncMock(return_value=mock_term_resp)

    # scrape_subject fetches the subject lookup (GET get_subject) once per
    # run, before its pagination loop. An empty lookup is intentionally unresolved;
    # section-only tests keep working while prerequisites retain previous data.
    mock_subject_resp = AsyncMock()
    mock_subject_resp.status = 200
    mock_subject_resp.headers = {"content-type": "application/json"}
    mock_subject_resp.text = AsyncMock(return_value="[]")
    mock_page.request.get = AsyncMock(return_value=mock_subject_resp)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_pw_cm


# Reserved fake subject keeps these scenarios recognizable. Before test database
# isolation, using a real subject here once deleted 598 production CS sections.
# The verified disposable database and private per-test schema now isolate these
# writes. The assertion below also catches unexpected fixture data in that schema.
_FAKE_SUBJECT = "ZZZ"


async def _assert_fake_subject_is_actually_empty(db_session) -> None:
    from sqlalchemy import text
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM sections WHERE course_code ~ :pattern"),
        {"pattern": f"^{_FAKE_SUBJECT}[0-9]"},
    )
    count = result.scalar()
    assert count == 0, (
        f"Test subject '{_FAKE_SUBJECT}' has {count} unexpected row(s) in its private schema. "
        "Check fixture setup before testing subject-wide deletion."
    )


# ─── Scraper health log ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_blocked_subject_continues_remaining_subjects(db_session):
    """A block on subject A must not prevent subject B from running."""
    from src.scrapers.banner import run_banner_scrape, BannerBlockedError

    call_log: list[str] = []

    async def mock_scrape(session, subject, term, *, progress=None):
        call_log.append(subject)
        if subject == "CS":
            raise BannerBlockedError("blocked")
        return (3, 0, 0)

    with patch("src.scrapers.banner.scrape_subject", side_effect=mock_scrape):
        await run_banner_scrape(db_session, ["CS", "MATH"], "202690")

    assert "MATH" in call_log, "MATH must be scraped even though CS was blocked"


@pytest.mark.asyncio
async def test_schema_change_aborts_remaining_subjects(db_session):
    """A BannerSchemaError on one subject must abort all subsequent subjects."""
    from src.scrapers.banner import run_banner_scrape, BannerSchemaError

    call_log: list[str] = []

    async def mock_scrape(session, subject, term, *, progress=None):
        call_log.append(subject)
        if subject == "CS":
            raise BannerSchemaError("key missing")
        return (3, 0, 0)

    with patch("src.scrapers.banner.scrape_subject", side_effect=mock_scrape):
        await run_banner_scrape(db_session, ["CS", "MATH", "PHYS"], "202690")

    assert "MATH" not in call_log, "Schema change must abort remaining subjects"
    assert "PHYS" not in call_log


# ─── Postgres RMP cache ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rmp_cache_hit_skips_http_request(db_session):
    """A cached professor must not trigger an HTTP request to RMP."""
    from src.scrapers.rmp import fetch_rmp_rating, _set_cached_rmp
    import httpx

    await _set_cached_rmp(db_session, "Dr. Smith", {"rmp_score": 4.5})

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=AssertionError("HTTP request made despite cache hit")
    )

    result = await fetch_rmp_rating(db_session, "Dr. Smith", mock_client)
    assert result is not None
    assert result["rmp_score"] == 4.5


@pytest.mark.asyncio
async def test_rmp_cache_miss_makes_exactly_one_http_request(db_session):
    """Cache miss → exactly one HTTP request, result cached afterward."""
    from src.scrapers.rmp import fetch_rmp_rating, _get_cached_rmp
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {"newSearch": {"teachers": {"edges": []}}}
    }

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    result = await fetch_rmp_rating(db_session, "Unknown Prof", mock_client)
    assert result is None  # not found on RMP
    assert mock_client.post.call_count == 1

    # Must have cached the not-found result
    cached = await _get_cached_rmp(db_session, "Unknown Prof")
    assert cached is False, "Not-found result must be cached as False"


@pytest.mark.asyncio
async def test_rmp_401_raises_auth_error_and_does_not_retry(db_session):
    """401 from RMP → RMPAuthError raised immediately, no retries."""
    from src.scrapers.rmp import fetch_rmp_rating, RMPAuthError
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 401

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(RMPAuthError) as exc:
        await fetch_rmp_rating(db_session, "Some Prof", mock_client)

    assert "401" in str(exc.value)
    assert mock_client.post.call_count == 1, "Must not retry a 401"


@pytest.mark.asyncio
async def test_rmp_schema_error_not_cached(db_session):
    """Unexpected RMP response shape → RMPSchemaError, nothing cached."""
    from src.scrapers.rmp import fetch_rmp_rating, RMPSchemaError, _get_cached_rmp
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"unexpected": "structure"}

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(RMPSchemaError):
        await fetch_rmp_rating(db_session, "Prof X", mock_client)

    cached = await _get_cached_rmp(db_session, "Prof X")
    assert cached is None, "Schema error must not cache a result"


@pytest.mark.asyncio
async def test_rmp_expired_cache_returns_none(db_session):
    """Expired cache entry must return None (forces a fresh request)."""
    from src.scrapers.rmp import _set_cached_rmp, _get_cached_rmp
    from sqlalchemy import text
    from datetime import datetime, timezone, timedelta

    # Seed with an already-expired entry
    await _set_cached_rmp(db_session, "Old Prof", {"rmp_score": 3.0})
    await db_session.execute(
        text(
            "UPDATE rmp_cache SET expires_at = :past WHERE professor_name = 'Old Prof'"
        ),
        {"past": datetime.now(timezone.utc) - timedelta(hours=1)},
    )
    await db_session.commit()

    result = await _get_cached_rmp(db_session, "Old Prof")
    assert result is None, "Expired cache must return None"
