"""Credit/title correctness through real scraping, storage, API, and planning."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.scrapers import banner
from tests.scrapers.test_banner_responses import TERM, SUBJECT, response, results, section, upstream


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


async def saved(session):
    row = (await session.execute(text("SELECT * FROM courses WHERE course_code='ZZZ997'"))).mappings().one()
    await session.rollback()
    return row


async def scrape(session, source, rows):
    source.pages = [response(results(rows[offset:offset + 2], len(rows))) for offset in range(0, len(rows), 2)]
    return await banner.scrape_subject(session, SUBJECT, TERM)


@pytest.mark.parametrize("fields,expected", [
    ({"creditHours": 1}, ("fixed", 1, 1)),
    ({"creditHourLow": 4, "creditHours": None}, ("fixed", 4, 4)),
    ({"creditHours": "0.50"}, ("fixed", .5, .5)),
    ({"creditHours": 0}, ("fixed", 0, 0)),
    ({"creditHourLow": 4, "creditHourHigh": 4}, ("fixed", 4, 4)),
    ({"creditHourLow": 1, "creditHourHigh": 6, "creditHourIndicator": "TO"}, ("range", 1, 6)),
    ({"creditHours": 3, "creditHourLow": 1, "creditHourHigh": 6, "creditHourIndicator": "OR"}, None),
    ({"creditHours": 3, "creditHourLow": 1, "creditHourHigh": 6, "creditHourIndicator": "TO"}, ("range", 1, 6)),
    ({"creditHourLow": 1, "creditHourHigh": 4, "creditHourIndicator": "OR"}, ("options", 1, 4)),
    ({}, None), ({"creditHours": None}, None),
    ({"creditHours": True}, None), ({"creditHours": -1}, None),
    ({"creditHours": "NaN"}, None), ({"creditHours": float("inf")}, None),
    ({"creditHours": []}, None), ({"creditHours": "3 credits"}, None),
    ({"creditHours": .33333}, None), ({"creditHours": 101}, None),
    ({"creditHours": 3, "creditHourLow": 4}, None),
    ({"creditHourHigh": 4}, None),
    ({"creditHourLow": 4, "creditHourHigh": 1, "creditHourIndicator": "TO"}, None),
    ({"creditHourLow": 1, "creditHourHigh": 4}, None),
    ({"creditHours": 3, "creditHourIndicator": "unknown"}, None),
])
def test_banner_credit_values_preserve_fixed_variable_and_unknown(fields, expected):
    from src.scrapers.course_metadata import parse_credits
    value, error = parse_credits(fields)
    if expected is None:
        assert value is None and error
    else:
        assert error is None
        assert (value["kind"], value["minimum"], value["maximum"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("credits", [0, 1, 4, .5])
async def test_scrape_stores_actual_credits_and_authoritative_title(db_session, upstream, credits):
    await scrape(db_session, upstream, [section(courseTitle="Synthetic &amp; Verified", creditHourLow=credits)])
    row = await saved(db_session)
    assert row["credits"] == credits
    assert row["title"] == "Synthetic & Verified"
    assert decode(row["credits_source"])["term"] == TERM
    assert decode(row["credits_source"])["url"].endswith("/searchResults/searchResults")
    assert decode(row["title_source"])["observations"][0]["crn"] == "81111"


@pytest.mark.asyncio
async def test_unknown_course_does_not_acquire_three_credits_or_an_invented_title(db_session, upstream):
    await scrape(db_session, upstream, [section()])
    row = await saved(db_session)
    assert row["credits"] is None
    assert row["title"] is None
    assert row["credits_source"] is None and row["title_source"] is None
    attempt = decode(row["metadata_latest_attempt"])
    assert attempt["credits_error"] and attempt["title_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("indicator", ["TO", "OR"])
async def test_variable_credits_never_become_a_fixed_number(db_session, upstream, indicator):
    # A previously verified scalar must be cleared when the source becomes variable.
    await scrape(db_session, upstream, [section(creditHours=3)])
    await scrape(db_session, upstream, [section(creditHourLow=1, creditHourHigh=4, creditHourIndicator=indicator)])
    row = await saved(db_session)
    assert row["credits"] is None
    value = decode(row["credits_source"])["value"]
    assert value == {"kind": "range" if indicator == "TO" else "options", "minimum": 1, "maximum": 4}
    await scrape(db_session, upstream, [section(creditHours=4)])
    recovered = await saved(db_session)
    assert recovered["credits"] == 4
    assert decode(recovered["credits_source"])["value"]["kind"] == "fixed"


@pytest.mark.asyncio
async def test_success_refreshes_existing_defaults_and_title_then_failure_preserves_sources(db_session, upstream):
    await db_session.execute(text("INSERT INTO courses(course_code,title,credits) VALUES ('ZZZ997','Old title',3)"))
    await db_session.commit()
    await scrape(db_session, upstream, [section(courseTitle="Correct title", creditHours=4)])
    original = await saved(db_session)
    assert (original["credits"], original["title"]) == (4, "Correct title")
    await scrape(db_session, upstream, [section(courseTitle="", creditHours="unavailable")])
    retained = await saved(db_session)
    for name in ("credits", "title", "credits_source", "title_source"):
        assert retained[name] == original[name]
    assert decode(retained["metadata_latest_attempt"])["credits_error"]


@pytest.mark.asyncio
async def test_independent_valid_field_can_refresh_when_other_metadata_is_missing(db_session, upstream):
    await scrape(db_session, upstream, [section(courseTitle="Known title", creditHours=1)])
    await scrape(db_session, upstream, [section(creditHours=4)])
    row = await saved(db_session)
    assert (row["credits"], row["title"]) == (4, "Known title")
    assert decode(row["metadata_latest_attempt"])["title_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"creditHours": 1}, {"courseTitle": "Different topic"}, {"creditHours": None}])
async def test_conflicting_or_incomplete_sections_preserve_previous_metadata_across_pages(db_session, upstream, changed):
    await scrape(db_session, upstream, [section(courseTitle="Previous title", creditHours=4)])
    original = await saved(db_session)
    rows = [section(str(81000 + i), courseTitle="New title", creditHours=3) for i in range(3)]
    rows[-1].update(changed)
    await scrape(db_session, upstream, rows)
    row = await saved(db_session)
    field = "title" if "courseTitle" in changed else "credits"
    assert row[field] == original[field]
    assert row[f"{field}_source"] == original[f"{field}_source"]
    assert decode(row["metadata_latest_attempt"])[f"{field}_error"]


@pytest.mark.asyncio
async def test_incomplete_subject_does_not_promote_partial_metadata(db_session, upstream):
    await scrape(db_session, upstream, [section(courseTitle="Original", creditHours=4)])
    original = await saved(db_session)
    upstream.pages = [response(results([section("81112", creditHours=1), section("81113", creditHours=1)], 3)), response({"success": False})]
    with pytest.raises(banner.BannerResponseError):
        await banner.scrape_subject(db_session, SUBJECT, TERM)
    row = await saved(db_session)
    assert row["credits_source"] == original["credits_source"]
    assert row["credits"] == 4


@pytest.mark.asyncio
async def test_failed_section_write_does_not_promote_metadata(db_session, upstream, monkeypatch):
    await scrape(db_session, upstream, [section(courseTitle="Original", creditHours=4)])
    monkeypatch.setattr(banner, "_upsert_section_with_meetings", AsyncMock(side_effect=ValueError("synthetic failure")))
    await scrape(db_session, upstream, [section(creditHours=1)])
    assert (await saved(db_session))["credits"] == 4


@pytest.mark.asyncio
async def test_metadata_replacement_is_atomic_on_database_rejection(db_session, upstream):
    await scrape(db_session, upstream, [section(courseTitle="Original", creditHours=4)])
    original = await saved(db_session)
    await db_session.execute(text("ALTER TABLE courses ADD CONSTRAINT reject_one CHECK (credits <> 1)"))
    await db_session.commit()
    await scrape(db_session, upstream, [section(courseTitle="Rejected title", creditHours=1)])
    row = await saved(db_session)
    for name in ("credits", "title", "credits_source", "title_source"):
        assert row[name] == original[name]
    assert decode(row["metadata_latest_attempt"])["save_error"] == "Could not save course metadata."


@pytest.mark.asyncio
@pytest.mark.parametrize("credits,expected,estimated", [(1, 1, False), (4, 4, False), (0, 0, False), (None, 3, True)])
async def test_planner_uses_verified_credits_and_labels_estimates(db_session, upstream, credits, expected, estimated):
    from src.services.plan import generate_plan
    from src.schemas.plan import ParsedDegreeValidated, StillNeededItem
    await scrape(db_session, upstream, [section(courseTitle="Synthetic course", creditHours=credits)])
    plan = await generate_plan(ParsedDegreeValidated(
        majors=["Synthetic major"], credits_remaining=4,
        still_needed=[StillNeededItem(requirement="Synthetic requirement", options=["ZZZ997"])],
    ), {"courses": [], "credits_per_semester": 4}, db_session)
    course = next(c for s in plan.semesters for c in s.courses if c.course_code == "ZZZ997")
    assert course.credits == expected
    assert course.credits_estimated is estimated
    assert bool(course.credits_note) is estimated
    assert course.title_status == "verified"


@pytest.mark.asyncio
async def test_variable_plan_estimate_uses_a_valid_bound_and_explains_it(db_session, upstream):
    from src.services.plan import generate_plan
    from src.schemas.plan import ParsedDegreeValidated, StillNeededItem
    await scrape(db_session, upstream, [section(creditHourLow=1, creditHourHigh=4, creditHourIndicator="OR")])
    plan = await generate_plan(ParsedDegreeValidated(
        majors=["Synthetic"], still_needed=[StillNeededItem(requirement="Research", options=["ZZZ997"])],
    ), {"courses": [], "credits_per_semester": 4}, db_session)
    course = next(c for s in plan.semesters for c in s.courses if c.course_code == "ZZZ997")
    assert course.credits == 4 and course.credits_estimated
    assert "1 or 4" in course.credits_note


@pytest.mark.asyncio
async def test_legacy_credit_value_is_an_estimate_until_refreshed(db_session):
    from src.services.plan import generate_plan
    from src.schemas.plan import ParsedDegreeValidated, StillNeededItem
    plan = await generate_plan(ParsedDegreeValidated(
        majors=["Synthetic"], still_needed=[StillNeededItem(requirement="Legacy", options=["CS999"])],
    ), {"courses": [], "credits_per_semester": 3}, db_session)
    course = next(c for s in plan.semesters for c in s.courses if c.course_code == "CS999")
    assert course.credits == 3 and course.credits_estimated
    assert "unverified" in course.credits_note.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_title", [None, "", "ZZZ997", 123, "Bad\x00Title", "\ud800", "T" * 513],
                         ids=["missing", "empty", "course-code", "numeric", "nul", "surrogate", "oversized"])
async def test_bad_title_keeps_previous_title_without_blocking_valid_credits(db_session, upstream, bad_title):
    await scrape(db_session, upstream, [section(courseTitle="Known", creditHours=1)])
    await scrape(db_session, upstream, [section(courseTitle=bad_title, creditHours=4)])
    row = await saved(db_session)
    assert (row["title"], row["credits"]) == ("Known", 4)
    assert decode(row["metadata_latest_attempt"])["title_error"]


@pytest.mark.asyncio
async def test_metadata_is_not_visible_before_commit_and_cancellation_preserves_it(db_session, db_session_factory, upstream, monkeypatch):
    await scrape(db_session, upstream, [section(courseTitle="Original", creditHours=4)])
    original = await saved(db_session)
    updated = asyncio.Event()
    release = asyncio.Event()
    execute = db_session.execute

    async def pause_before_commit(statement, params=None, **kwargs):
        result = await execute(statement, params, **kwargs)
        if params and "has_title" in params:
            updated.set()
            await release.wait()
        return result

    monkeypatch.setattr(db_session, "execute", pause_before_commit)
    task = asyncio.create_task(scrape(db_session, upstream, [section(courseTitle="Pending", creditHours=1)]))
    try:
        await asyncio.wait_for(updated.wait(), 5)
        async with db_session_factory() as observer:
            assert (await observer.execute(text("SELECT title,credits FROM courses WHERE course_code='ZZZ997'"))).one() == ("Original", 4)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    row = await saved(db_session)
    for name in ("title", "credits", "title_source", "credits_source", "metadata_latest_attempt"):
        assert row[name] == original[name]
