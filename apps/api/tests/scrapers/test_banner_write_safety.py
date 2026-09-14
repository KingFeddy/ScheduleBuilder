"""Failed catalog writes must preserve existing sections and complete schedules."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.scrapers import banner
from tests.scrapers.test_banner import banner_pattern
from tests.scrapers.test_banner_responses import (
    SUBJECT, TERM, response, results, section, seed_catalog,
    upstream as upstream,
)

pytestmark = pytest.mark.asyncio


async def _seed(session):
    await seed_catalog(session)
    await session.execute(text("""
        INSERT INTO sections (crn, term, course_code, open_seats, total_seats)
        VALUES ('89997', :term, 'ZZZ997', 7, 20)
    """), {"term": TERM})
    await session.execute(text("""
        INSERT INTO meetings (crn, term, days, start_time, end_time, location)
        VALUES ('89997', :term, 'T', '09:00', '10:00', 'Stale room')
    """), {"term": TERM})
    await session.commit()


async def _snapshot(factory, crn="89999", term=TERM):
    # Read through an independent session so inspection cannot change the
    # worker's transaction or accidentally observe its uncommitted writes.
    async with factory() as observer:
        params = {"crn": crn, "term": term}
        row = (await observer.execute(text("""
            SELECT * FROM sections WHERE crn = :crn AND term = :term
        """), params)).mappings().one_or_none()
        meetings = (await observer.execute(text("""
            SELECT * FROM meetings WHERE crn = :crn AND term = :term ORDER BY id
        """), params)).mappings().all()
        return {"section": dict(row) if row else None, "meetings": [dict(item) for item in meetings]}


def _updated_section(crn="89999", failure=None, **overrides):
    first = banner_pattern(monday=True, begin_time="1200", end_time="1300", building="Good", room="room")
    second = banner_pattern(tuesday=True, begin_time="1400", end_time="1500", building="Second", room="room")
    if failure in ("sql", "commit"):
        second["building"] = "Rejected"
    elif failure == "parse":
        second["meetingTime"]["beginTime"] = "2500"
    elif failure == "partial":
        second["meetingTime"]["endTime"] = None
    elif failure == "reversed":
        second["meetingTime"]["beginTime"] = "1600"
    return section(
        crn, meetingsFaculty=[first, second],
        seatsAvailable=None if failure == "before-write" else 2,
        faculty=[{"displayName": "New Professor", "primaryIndicator": True}],
        sequenceNumber="002", **overrides,
    )


async def _failure_constraint(session, failure):
    if failure == "sql":
        await session.execute(text("""
            ALTER TABLE meetings ADD CONSTRAINT reject_test_room
            CHECK (location IS DISTINCT FROM 'Rejected room')
        """))
    elif failure == "commit":
        # Actual deferred PostgreSQL failure: all INSERTs succeed, but COMMIT
        # must reject the second meeting and roll back the whole section.
        await session.execute(text("CREATE TABLE allowed_test_rooms (name TEXT PRIMARY KEY)"))
        await session.execute(text("""
            INSERT INTO allowed_test_rooms (name)
            VALUES ('Preserved room'), ('Stale room'), ('Good room'), ('Second room')
        """))
        await session.execute(text("""
            ALTER TABLE meetings ADD CONSTRAINT allowed_test_room
            FOREIGN KEY (location) REFERENCES allowed_test_rooms(name)
            DEFERRABLE INITIALLY DEFERRED
        """))
    await session.commit()


def _queue(upstream, rows):
    upstream.pages.extend(
        response(results(rows[offset:offset + 2], len(rows)))
        for offset in range(0, len(rows), 2)
    )


@pytest.mark.parametrize("failure", ["before-write", "parse", "sql", "commit", "partial", "reversed"])
@pytest.mark.parametrize("later_page", [False, True], ids=["first-page", "later-page"])
async def test_failed_section_preserves_entire_subject_catalog(
    db_session, db_session_factory, upstream, monkeypatch, failure, later_page,
):
    await _seed(db_session)
    await _failure_constraint(db_session, failure)
    before = await _snapshot(db_session_factory)
    stale_before = await _snapshot(db_session_factory, "89997")
    cleanup = AsyncMock(wraps=banner._delete_stale_sections)
    monkeypatch.setattr(banner, "_delete_stale_sections", cleanup)
    bad = _updated_section(failure=failure)
    good = [section("81111"), section("81112")]
    _queue(upstream, [*good, bad] if later_page else [bad, *good])

    counts = await banner.scrape_subject(db_session, SUBJECT, TERM)

    assert await _snapshot(db_session_factory) == before
    assert await _snapshot(db_session_factory, "89997") == stale_before
    assert counts == (2, 1, 0)
    cleanup.assert_not_awaited()
    assert not db_session.in_transaction()
    for crn in ("81111", "81112"):
        assert (await _snapshot(db_session_factory, crn))["section"]["open_seats"] == 5
    assert (await _snapshot(db_session_factory, "89999", "202610"))["section"]["open_seats"] == 9
    assert (await _snapshot(db_session_factory, "89998"))["section"]["open_seats"] == 10
    upstream.browser.close.assert_awaited_once()


async def test_cleanup_resumes_only_after_a_fully_successful_retry(db_session, db_session_factory, upstream):
    await _seed(db_session)
    await _failure_constraint(db_session, "sql")
    _queue(upstream, [_updated_section(failure="sql"), section("81111")])
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (1, 1, 0)
    assert (await _snapshot(db_session_factory, "89997"))["section"] is not None

    _queue(upstream, [_updated_section(), section("81111")])
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (2, 0, 1)
    repaired = await _snapshot(db_session_factory)
    assert repaired["section"]["open_seats"] == 2
    assert [item["location"] for item in repaired["meetings"]] == ["Good room", "Second room"]
    assert await _snapshot(db_session_factory, "89997") == {"section": None, "meetings": []}


async def test_all_failed_upserts_leave_rows_intact_and_record_failure(db_session, db_session_factory, upstream):
    await _seed(db_session)
    await _failure_constraint(db_session, "commit")
    originals = {crn: await _snapshot(db_session_factory, crn) for crn in ("89999", "89997")}
    _queue(upstream, [_updated_section(crn, failure="commit") for crn in originals])

    await banner.run_banner_scrape(db_session, [SUBJECT], TERM)

    for crn, original in originals.items():
        assert await _snapshot(db_session_factory, crn) == original
    run = (await db_session.execute(text("""
        SELECT status, sections_upserted, sections_failed, finished_at FROM scraper_runs
        WHERE scraper = 'banner' AND subject IS NULL
    """))).one()
    assert (run.status, run.sections_upserted, run.sections_failed) == ("failed", 0, 2)
    assert run.finished_at is not None


@pytest.mark.parametrize("failure", ["parse", "sql", "commit"])
async def test_failed_new_section_rolls_back_course_stub_and_meetings(db_session, db_session_factory, failure):
    await _seed(db_session)
    await _failure_constraint(db_session, failure)
    original = await _snapshot(db_session_factory)
    with pytest.raises((ValueError, DBAPIError)):
        await banner._upsert_section_with_meetings(
            db_session, _updated_section("81113", failure=failure, courseNumber="998"), TERM,
        )
    assert not db_session.in_transaction()
    assert await _snapshot(db_session_factory, "81113") == {"section": None, "meetings": []}
    assert await _snapshot(db_session_factory) == original
    assert await db_session.scalar(text("SELECT count(*) FROM courses WHERE course_code = 'ZZZ998'")) == 0


@pytest.mark.parametrize("outcome", ["success", "sql-error", "cancel"])
async def test_readers_never_observe_a_partial_section_replacement(
    db_session, db_session_factory, monkeypatch, outcome,
):
    await _seed(db_session)
    await _failure_constraint(db_session, "sql")
    original = await _snapshot(db_session_factory)
    entered = asyncio.Event()
    proceed = asyncio.Event()
    execute = db_session.execute
    inserts = 0

    async def pause_before_second_meeting(statement, params=None, **kwargs):
        nonlocal inserts
        if "INSERT INTO meetings" in str(statement):
            inserts += 1
            if inserts == 2:
                # The section UPDATE, meeting DELETE, and first replacement
                # INSERT have already executed, but have not committed.
                entered.set()
                await proceed.wait()
        return await execute(statement, params, **kwargs)

    monkeypatch.setattr(db_session, "execute", pause_before_second_meeting)
    task = asyncio.create_task(banner._upsert_section_with_meetings(
        db_session, _updated_section(failure="sql" if outcome == "sql-error" else None), TERM,
    ))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            assert await _snapshot(db_session_factory) == original
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                proceed.set()
                if outcome == "sql-error":
                    with pytest.raises(DBAPIError):
                        await task
                else:
                    await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert not db_session.in_transaction()
    after = await _snapshot(db_session_factory)
    if outcome == "success":
        assert after["section"]["open_seats"] == 2
        assert after["section"]["professor_name"] == "New Professor"
        assert after["section"]["section_number"] == "002"
        assert [item["location"] for item in after["meetings"]] == ["Good room", "Second room"]
    else:
        assert after == original


@pytest.mark.parametrize("patterns", [[], [banner_pattern(monday=True)]], ids=["asynchronous", "tba"])
async def test_valid_async_or_tba_replacement_still_commits(db_session, db_session_factory, patterns):
    await _seed(db_session)
    await banner._upsert_section_with_meetings(
        db_session, section("89999", meetingsFaculty=patterns), TERM,
    )
    saved = await _snapshot(db_session_factory)
    assert saved["section"]["open_seats"] == 5
    assert len(saved["meetings"]) == len(patterns)
    for meeting in saved["meetings"]:
        assert meeting["days"] == "M"
        assert meeting["start_time"] is None and meeting["end_time"] is None
