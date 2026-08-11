"""
Step 0 — Resilience tests that prove gaps in the current scraper implementation.

All tests that import from lock.py, rmp.py, or new banner.py symbols will fail
with ImportError until those are implemented (Steps 2–5). DB-dependent tests
require a db_session fixture (conftest.py) and will fail until it exists.

Run after each implementation step to watch the failure count drop.
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Schema validation ────────────────────────────────────────────────────────

def test_valid_section_passes_schema_validation():
    from src.scrapers.banner import _validate_section_schema

    valid = {
        "courseReferenceNumber": "12345",
        "subject": "CS",
        "courseNumber": "280",
        "meetingsFaculty": [
            {"meetingTime": {"monday": True, "beginTime": "1000", "endTime": "1115"}}
        ],
    }
    _validate_section_schema(valid)  # must not raise


def test_missing_meetings_faculty_raises_schema_error():
    from src.scrapers.banner import _validate_section_schema, BannerSchemaError

    invalid = {
        "courseReferenceNumber": "12345",
        "subject": "CS",
        "courseNumber": "280",
        # meetingsFaculty absent
    }
    with pytest.raises(BannerSchemaError) as exc:
        _validate_section_schema(invalid)
    assert "meetingsFaculty" in str(exc.value)


def test_missing_required_section_key_raises_schema_error():
    from src.scrapers.banner import _validate_section_schema, BannerSchemaError

    # courseNumber missing
    invalid = {
        "courseReferenceNumber": "12345",
        "subject": "CS",
        "meetingsFaculty": [],
    }
    with pytest.raises(BannerSchemaError):
        _validate_section_schema(invalid)


def test_missing_meeting_time_key_raises_schema_error():
    from src.scrapers.banner import _validate_section_schema, BannerSchemaError

    invalid = {
        "courseReferenceNumber": "12345",
        "subject": "CS",
        "courseNumber": "280",
        "meetingsFaculty": [{"someOtherKey": "value"}],  # meetingTime absent
    }
    with pytest.raises(BannerSchemaError):
        _validate_section_schema(invalid)


def test_empty_meetings_faculty_passes_schema_validation():
    from src.scrapers.banner import _validate_section_schema

    # Async-only sections have no meeting patterns — valid
    section = {
        "courseReferenceNumber": "12345",
        "subject": "CS",
        "courseNumber": "280",
        "meetingsFaculty": [],
    }
    _validate_section_schema(section)  # must not raise


# ─── HTTP response handling ───────────────────────────────────────────────────

def test_403_raises_blocked_error():
    from src.scrapers.banner import _fetch_page, BannerBlockedError

    async def run():
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.headers = {"content-type": "application/json"}
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_response)

        with pytest.raises(BannerBlockedError):
            await _fetch_page(mock_page, "https://example.com", {})

    asyncio.run(run())


def test_html_200_raises_blocked_error():
    from src.scrapers.banner import _fetch_page, BannerBlockedError

    async def run():
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.text = AsyncMock(return_value="<html>Session expired</html>")
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_response)

        with pytest.raises(BannerBlockedError):
            await _fetch_page(mock_page, "https://example.com", {})

    asyncio.run(run())


def test_non_json_200_raises_blocked_error():
    from src.scrapers.banner import _fetch_page, BannerBlockedError

    async def run():
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = AsyncMock(return_value="not valid json {{{{")
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_response)

        with pytest.raises(BannerBlockedError):
            await _fetch_page(mock_page, "https://example.com", {})

    asyncio.run(run())


def test_valid_json_response_returns_parsed_dict():
    from src.scrapers.banner import _fetch_page

    async def run():
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = AsyncMock(
            return_value='{"data": [], "totalCount": 0}'
        )
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_response)

        result = await _fetch_page(mock_page, "https://example.com", {})
        assert result == {"data": [], "totalCount": 0}

    asyncio.run(run())


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
    # run, before its pagination loop — give it a harmless empty list so
    # tests that don't care about prerequisites don't crash on this call.
    mock_subject_resp = AsyncMock()
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


# Reserved fake subject for these tests only — NOT a real NJIT subject code.
# _delete_stale_sections matches by subject prefix across the WHOLE subject,
# so a test that used a real subject (e.g. "CS") would delete every real
# section for that subject the mocked response didn't happen to include.
# This exact mistake shipped once and deleted all 598 real CS sections from
# production before being caught and recovered — never reuse a real subject
# code here, and the assertion below is a second guardrail against it
# happening silently again.
_FAKE_SUBJECT = "ZZZ"


async def _assert_fake_subject_is_actually_empty(db_session) -> None:
    from sqlalchemy import text
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM sections WHERE course_code ~ :pattern"),
        {"pattern": f"^{_FAKE_SUBJECT}[0-9]"},
    )
    count = result.scalar()
    assert count == 0, (
        f"Test subject '{_FAKE_SUBJECT}' has {count} real row(s) in the database — "
        "STOP. Do not run this test until a genuinely unused subject prefix is chosen; "
        "this test deletes everything matching it."
    )


@pytest.mark.asyncio
async def test_stale_section_removed_after_complete_scrape(db_session):
    """
    A section Banner no longer returns for a subject it fully, successfully
    scraped must be deleted — upserts alone never remove anything, so a
    cancelled/removed CRN would otherwise sit stale in the DB forever.
    """
    from src.scrapers.banner import scrape_subject
    from sqlalchemy import text

    await _assert_fake_subject_is_actually_empty(db_session)

    # Seed the stub courses these sections need (FK), then a stale section
    # Banner will NOT return in the mocked response below.
    await db_session.execute(text("""
        INSERT INTO courses (course_code, title, credits) VALUES
            ('ZZZ997', 'Test Stale Course', 3),
            ('ZZZ996', 'Test Seen Course', 3)
        ON CONFLICT (course_code) DO NOTHING
    """))
    await db_session.execute(text("""
        INSERT INTO sections (crn, term, course_code, total_seats, open_seats, scraped_at)
        VALUES ('77777', '202690', 'ZZZ997', 30, 10, NOW())
        ON CONFLICT (crn, term) DO NOTHING
    """))
    await db_session.commit()

    banner_section = {
        "courseReferenceNumber": "89999",
        "subject": _FAKE_SUBJECT,
        "courseNumber": "996",
        "seatsAvailable": 5,
        "maximumEnrollment": 20,
        "meetingsFaculty": [],
    }
    mock_pw_cm = _mock_playwright_returning([banner_section])

    async def fake_fetch_page(page, url, params, timeout_ms=30_000):
        return {"data": [banner_section], "totalCount": 1}

    with patch("src.scrapers.banner.async_playwright", return_value=mock_pw_cm):
        with patch("src.scrapers.banner._fetch_page", side_effect=fake_fetch_page):
            upserted, failed, deleted = await scrape_subject(db_session, _FAKE_SUBJECT, "202690")

    assert upserted == 1
    assert failed == 0
    assert deleted == 1, "The stale ZZZ997 section must be counted as deleted"

    stale = await db_session.execute(
        text("SELECT 1 FROM sections WHERE crn = '77777' AND term = '202690'")
    )
    assert stale.first() is None, "Stale section must be removed after a complete scrape"

    seen = await db_session.execute(
        text("SELECT 1 FROM sections WHERE crn = '89999' AND term = '202690'")
    )
    assert seen.first() is not None, "Section Banner did return must still exist"


@pytest.mark.asyncio
async def test_stale_sections_survive_an_incomplete_scrape(db_session):
    """
    If the scrape is blocked before completing, "not seen this run" doesn't
    mean "Banner removed it" — it means we never got far enough to check.
    Deleting here would silently corrupt data on every transient block.
    """
    from src.scrapers.banner import scrape_subject, BannerBlockedError
    from sqlalchemy import text

    await _assert_fake_subject_is_actually_empty(db_session)

    await db_session.execute(text("""
        INSERT INTO courses (course_code, title, credits) VALUES ('ZZZ995', 'Test Course', 3)
        ON CONFLICT (course_code) DO NOTHING
    """))
    await db_session.execute(text("""
        INSERT INTO sections (crn, term, course_code, total_seats, open_seats, scraped_at)
        VALUES ('66666', '202690', 'ZZZ995', 30, 10, NOW())
        ON CONFLICT (crn, term) DO NOTHING
    """))
    await db_session.commit()

    mock_pw_cm = _mock_playwright_returning([])

    with patch("src.scrapers.banner.async_playwright", return_value=mock_pw_cm):
        with patch(
            "src.scrapers.banner._fetch_page",
            side_effect=BannerBlockedError("403"),
        ):
            with pytest.raises(BannerBlockedError):
                await scrape_subject(db_session, _FAKE_SUBJECT, "202690")

    still_there = await db_session.execute(
        text("SELECT 1 FROM sections WHERE crn = '66666' AND term = '202690'")
    )
    assert still_there.first() is not None, "Nothing must be deleted on an incomplete scrape"


# ─── Concurrency guard ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_advisory_lock_prevents_concurrent_runs(db_session_factory):
    """
    Two separate DB sessions: session 1 holds the lock inside an open transaction,
    session 2 tries to acquire it and must get False.
    asyncpg requires separate connections for concurrent lock testing.
    """
    from src.scrapers.lock import advisory_lock, BANNER_SCRAPER_LOCK_ID
    from sqlalchemy import text

    async with db_session_factory() as s1, db_session_factory() as s2:
        # Open s1's transaction and acquire the lock — do NOT commit yet.
        await s1.begin()
        r1 = await s1.execute(
            text("SELECT pg_try_advisory_xact_lock(:id)"),
            {"id": BANNER_SCRAPER_LOCK_ID},
        )
        assert r1.scalar() is True, "Session 1 must acquire the lock"

        # While s1's transaction is still open, s2 should fail to acquire.
        async with advisory_lock(s2, BANNER_SCRAPER_LOCK_ID, "test") as acquired:
            assert acquired is False, "Session 2 must not acquire a held lock"

        await s1.rollback()  # release lock


@pytest.mark.asyncio
async def test_skipped_overlap_recorded_in_scraper_runs(db_session):
    """
    When the advisory lock is already held, run_banner_scrape must write a
    skipped_overlap record and return without scraping.
    Lock is simulated via mock — true concurrent connections are tested separately.
    """
    from src.scrapers.banner import run_banner_scrape
    from sqlalchemy import text
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lock_already_held(session, lock_id, name):
        yield False  # simulate: another instance holds the lock

    with patch("src.scrapers.banner.advisory_lock", lock_already_held):
        await run_banner_scrape(db_session, ["CS"], "202690")

    result = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM scraper_runs
            WHERE scraper = 'banner' AND status = 'skipped_overlap'
            AND started_at > NOW() - INTERVAL '5 minutes'
        """)
    )
    assert result.scalar() >= 1, "Held lock must produce a skipped_overlap record"


# ─── Scraper health log ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_scrape_creates_completed_record(db_session):
    """Successful scrape → scraper_runs row with status='completed'."""
    from src.scrapers.banner import run_banner_scrape
    from sqlalchemy import text

    with patch("src.scrapers.banner.scrape_subject", return_value=(5, 0, 0)):
        await run_banner_scrape(db_session, ["CS"], "202690")

    result = await db_session.execute(
        text(
            "SELECT status, sections_upserted FROM scraper_runs "
            "WHERE scraper = 'banner' ORDER BY started_at DESC LIMIT 1"
        )
    )
    row = result.mappings().first()
    assert row is not None
    assert row["status"] == "completed"
    assert row["sections_upserted"] == 5


@pytest.mark.asyncio
async def test_banner_block_creates_blocked_record(db_session):
    """A BannerBlockedError → scraper_runs row with status='blocked'."""
    from src.scrapers.banner import run_banner_scrape, BannerBlockedError
    from sqlalchemy import text

    with patch(
        "src.scrapers.banner.scrape_subject",
        side_effect=BannerBlockedError("403"),
    ):
        await run_banner_scrape(db_session, ["CS"], "202690")

    result = await db_session.execute(
        text(
            "SELECT status FROM scraper_runs "
            "WHERE scraper = 'banner' AND status = 'blocked'"
        )
    )
    assert result.fetchone() is not None, "Block must produce a 'blocked' record"


@pytest.mark.asyncio
async def test_one_blocked_subject_continues_remaining_subjects(db_session):
    """A block on subject A must not prevent subject B from running."""
    from src.scrapers.banner import run_banner_scrape, BannerBlockedError

    call_log: list[str] = []

    async def mock_scrape(session, subject, term):
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

    async def mock_scrape(session, subject, term):
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
async def test_rmp_cache_not_found_stored_as_false(db_session):
    """Professor not found on RMP → cached as False (not None)."""
    from src.scrapers.rmp import _set_cached_rmp, _get_cached_rmp

    await _set_cached_rmp(db_session, "Ghost Prof", None)

    result = await _get_cached_rmp(db_session, "Ghost Prof")
    assert result is False, "Not-found must be cached as False, not None"


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
