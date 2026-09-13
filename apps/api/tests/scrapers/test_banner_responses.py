"""Banner failures must never authorize removal of existing catalog records."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

from src.scrapers import banner
from tests.scrapers.test_banner_resilience import _mock_playwright_returning

TERM = "202690"
SUBJECT = "ZZZ"


def section(crn="81111", **overrides):
    return {
        "courseReferenceNumber": crn, "subject": SUBJECT, "courseNumber": "997",
        "term": TERM, "meetingsFaculty": [], "seatsAvailable": 5,
        "maximumEnrollment": 20, **overrides,
    }


def results(rows=None, total=0, **overrides):
    return {"success": True, "data": [] if rows is None else rows, "totalCount": total, **overrides}


def response(payload, *, status=200, content_type="application/json"):
    return MagicMock(
        status=status, headers={"content-type": content_type},
        text=AsyncMock(return_value=json.dumps(payload)),
    )


@pytest.fixture
def upstream(monkeypatch):
    """Keep the actual HTTP parser/pagination code, replacing browser I/O only."""
    cm = _mock_playwright_returning([])
    browser = cm.__aenter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    source = SimpleNamespace(pages=[], requests=[], browser=browser)

    async def goto(url, **kwargs):
        if "/searchResults/searchResults?" in url:
            source.requests.append(parse_qs(urlsplit(url).query))
            if not source.pages:
                raise AssertionError("Unexpected extra Banner results request")
            item = source.pages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return response({})

    page.goto = AsyncMock(side_effect=goto)
    monkeypatch.setattr(banner, "async_playwright", MagicMock(return_value=cm))
    monkeypatch.setattr(banner, "fetch_subject_lookup", AsyncMock(return_value={}))
    async def empty_prerequisites(page, base, term, crn, lookup):
        from src.schemas.prerequisites import (
            AllConditions, ObservationScope, PrerequisiteRefresh, PrerequisiteRules,
        )
        return PrerequisiteRefresh(
            rules=PrerequisiteRules(
                scope=ObservationScope(term=term, crn=crn),
                prerequisites=AllConditions(items=[]), corequisites=AllConditions(items=[]),
            ), sources=[], status="verified_empty",
        )

    monkeypatch.setattr(banner, "fetch_prerequisites", empty_prerequisites)
    monkeypatch.setattr(banner, "PAGE_SIZE", 2)
    monkeypatch.setattr(banner, "RETRY_DELAYS", [0, 0])
    monkeypatch.setattr(banner, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    return source


async def seed_catalog(session):
    await session.execute(text("""
        INSERT INTO courses (course_code, title, credits)
        VALUES ('ZZZ997', 'Preserved course', 3)
    """))
    await session.execute(text("""
        INSERT INTO sections (crn, term, course_code, total_seats, open_seats)
        VALUES ('89999', :term, 'ZZZ997', 20, 8),
               ('89999', '202610', 'ZZZ997', 20, 9),
               ('89998', :term, 'CS999', 20, 10)
    """), {"term": TERM})
    await session.execute(text("""
        INSERT INTO meetings (crn, term, days, start_time, end_time, location)
        VALUES ('89999', :term, 'M', '10:00', '11:00', 'Preserved room')
    """), {"term": TERM})
    await session.commit()


INVALID_ENVELOPES = [
    pytest.param({"success": False, "data": None, "totalCount": 0}, id="failed-null"),
    pytest.param(results(success=False), id="failed-empty"),
    pytest.param(results(success="true"), id="string-success"),
    pytest.param(results(success=1), id="numeric-success"),
    pytest.param({"data": [], "totalCount": 0}, id="missing-success"),
    pytest.param({"success": True, "totalCount": 0}, id="missing-data"),
    pytest.param(results(data=None), id="null-data"),
    pytest.param(results(data={}), id="object-data"),
    pytest.param(results(data=""), id="string-data"),
    pytest.param({"success": True, "data": []}, id="missing-total"),
    pytest.param(results(total=None), id="null-total"),
    pytest.param(results(total=False), id="boolean-total"),
    pytest.param(results(total="0"), id="string-total"),
    pytest.param(results(total=0.0), id="fractional-total"),
    pytest.param(results(total=-1), id="negative-total"),
    pytest.param(None, id="null-root"),
    pytest.param([], id="array-root"),
    pytest.param("error", id="string-root"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", INVALID_ENVELOPES)
@pytest.mark.parametrize("later_page", [False, True], ids=["first-page", "later-page"])
async def test_invalid_results_preserve_catalog_and_do_not_complete_run(
    db_session, upstream, payload, later_page,
):
    await seed_catalog(db_session)
    if later_page:
        upstream.pages.append(response(results([section("81111"), section("81112")], 3)))
    upstream.pages.append(response(payload))

    await banner.run_banner_scrape(db_session, [SUBJECT], TERM)

    assert await db_session.scalar(text("""
        SELECT open_seats FROM sections WHERE crn = '89999' AND term = :term
    """), {"term": TERM}) == 8
    assert await db_session.scalar(text("""
        SELECT location FROM meetings WHERE crn = '89999' AND term = :term
    """), {"term": TERM}) == "Preserved room"
    run = (await db_session.execute(text("""
        SELECT status, sections_failed, finished_at FROM scraper_runs
        WHERE scraper = 'banner' AND subject IS NULL
    """))).one()
    assert run.status == ("partial" if later_page else "failed")
    # An invalid response cannot establish how many sections failed. A subject
    # failure is no longer incorrectly counted as one failed section.
    assert run.sections_failed is None
    assert run.finished_at is not None
    upstream.browser.close.assert_awaited_once()


BAD_PAGES = [
    pytest.param([results([], 2)], id="empty-before-total"),
    pytest.param([results([section()], 2)], id="short-first-page"),
    pytest.param([results([section()], 0)], id="rows-exceed-zero-total"),
    pytest.param([results([section("81111"), section("81112"), section("81113")], 3)], id="oversized-page"),
    pytest.param([results([section(), section()], 2)], id="duplicate-in-page"),
    pytest.param([results([section("81111"), section("81112")], 3), results([], 3)], id="empty-last-page"),
    pytest.param([results([section("81111"), section("81112")], 4), results([section("81113")], 4)], id="short-last-page"),
    pytest.param([results([section("81111"), section("81112")], 3), results([section("81111")], 3)], id="repeated-crn"),
    pytest.param([results([section("81111"), section("81112")], 3), results([], 2)], id="shrinking-total"),
    pytest.param([results([section("81111"), section("81112")], 3), results([section("81113"), section("81114")], 4)], id="growing-total"),
    pytest.param([results([section(subject="CS")], 1)], id="wrong-subject"),
    pytest.param([results([section(term="202610")], 1)], id="wrong-term"),
    pytest.param([results([section(), None], 2)], id="non-object-section"),
    pytest.param([results([section(), section("81112", meetingsFaculty=None)], 2)], id="invalid-section-structure"),
    pytest.param([results([section()], 1, pageOffset=1)], id="wrong-offset-echo"),
    pytest.param([results([section()], 1, pageMaxSize=1)], id="wrong-page-size-echo"),
    pytest.param([results([section()], 1, pageOffset="0")], id="untyped-offset-echo"),
    pytest.param([results([section()], 1, pageMaxSize=True)], id="boolean-page-size-echo"),
    pytest.param([results([section("81111"), section("81112")], 3),
                  results([section("81113")], 3, pageOffset=0)], id="wrong-later-offset-echo"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", BAD_PAGES)
async def test_inconsistent_pages_never_authorize_cleanup(db_session, upstream, pages):
    await seed_catalog(db_session)
    upstream.pages.extend(response(page) for page in pages)
    await banner.run_banner_scrape(db_session, [SUBJECT], TERM)

    assert await db_session.scalar(text("""
        SELECT count(*) FROM sections WHERE crn = '89999' AND term = :term
    """), {"term": TERM}) == 1
    # Each multi-page fixture starts with two valid rows; a later bad page
    # leaves that committed progress intact without claiming complete totals.
    expected_status = "failed" if len(pages) == 1 else "partial"
    assert (await db_session.execute(text("""
        SELECT status, sections_upserted, sections_failed FROM scraper_runs
        WHERE scraper = 'banner' AND subject IS NULL
    """))).one() == (expected_status, None, None)
    # Validate the entire bad page before applying even its valid first row.
    saved = (await db_session.execute(text("""
        SELECT crn, term FROM sections WHERE crn LIKE '811%' ORDER BY crn, term
    """))).all()
    assert saved == ([] if len(pages) == 1 else [("81111", TERM), ("81112", TERM)])
    upstream.browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [section()], [section("81111"), section("81112")],
                                      [section("81111"), section("81112"), section("81113")]])
async def test_complete_results_allow_scoped_cleanup(db_session, upstream, rows):
    await seed_catalog(db_session)
    pages = [rows[offset:offset + 2] for offset in range(0, len(rows), 2)] or [[]]
    upstream.pages.extend(
        response(results(page, len(rows), pageOffset=index * 2, pageMaxSize=2))
        for index, page in enumerate(pages)
    )

    upserted, failed, deleted = await banner.scrape_subject(db_session, SUBJECT, TERM)
    assert (upserted, failed, deleted) == (len(rows), 0, 1)
    assert [request["pageOffset"] for request in upstream.requests] == [[str(offset)] for offset in range(0, max(1, len(rows)), 2)]
    assert await db_session.scalar(text("SELECT count(*) FROM sections WHERE crn LIKE '811%'")) == len(rows)
    assert await db_session.scalar(text("SELECT count(*) FROM meetings WHERE crn = '89999' AND term = :term"), {"term": TERM}) == 0
    assert await db_session.scalar(text("SELECT open_seats FROM sections WHERE crn = '89999' AND term = '202610'")) == 9
    assert await db_session.scalar(text("SELECT open_seats FROM sections WHERE crn = '89998' AND term = :term"), {"term": TERM}) == 10
    upstream.browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 301, 400, 401, 403, 429, 500, 503])
async def test_http_failure_is_never_accepted_as_search_results(status):
    page = MagicMock(goto=AsyncMock(return_value=response(results(), status=status)))
    error = banner.BannerBlockedError if status in (401, 403) else banner.BannerResponseError
    with pytest.raises(error, match="Banner.*(?:HTTP|returned).*" + str(status)):
        await banner._fetch_page(page, "https://example.invalid/results", {})


@pytest.mark.asyncio
async def test_navigation_without_a_response_has_a_useful_error():
    page = MagicMock(goto=AsyncMock(return_value=None))
    with pytest.raises(banner.BannerResponseError, match="Banner.*no.*response"):
        await banner._fetch_page(page, "https://example.invalid/results", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["", "text/plain", "TEXT/HTML; charset=utf-8"])
async def test_non_json_content_type_is_rejected_even_with_json_body(content_type):
    page = MagicMock(goto=AsyncMock(return_value=response(results(), content_type=content_type)))
    with pytest.raises(banner.BannerBlockedError):
        await banner._fetch_page(page, "https://example.invalid/results", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["Application/JSON; charset=utf-8", "application/vnd.banner+json"])
async def test_json_media_types_are_accepted(content_type):
    page = MagicMock(goto=AsyncMock(return_value=response(results(), content_type=content_type)))
    assert await banner._fetch_page(page, "https://example.invalid/results", {}) == results()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], "error", False])
async def test_non_object_json_has_a_schema_error(payload):
    page = MagicMock(goto=AsyncMock(return_value=response(payload)))
    with pytest.raises(banner.BannerSchemaError, match="JSON object"):
        await banner._fetch_page(page, "https://example.invalid/results", {})


@pytest.mark.asyncio
async def test_http_failure_on_later_page_preserves_existing_rows(db_session, upstream):
    await seed_catalog(db_session)
    upstream.pages.append(response(results([section("81111"), section("81112")], 3)))
    upstream.pages.extend(response(results(), status=503) for _ in range(3))
    await banner.run_banner_scrape(db_session, [SUBJECT], TERM)
    assert await db_session.scalar(text("SELECT open_seats FROM sections WHERE crn = '89999' AND term = :term"), {"term": TERM}) == 8
    assert [item["pageOffset"] for item in upstream.requests] == [["0"], ["2"], ["2"], ["2"]]


@pytest.mark.parametrize("overrides", [
    {"courseReferenceNumber": None}, {"courseReferenceNumber": ""},
    {"courseReferenceNumber": 81111}, {"subject": " "}, {"courseNumber": None},
    {"meetingsFaculty": None}, {"meetingsFaculty": {}}, {"meetingsFaculty": [None]},
    {"meetingsFaculty": [{"meetingTime": None}]},
    {"meetingsFaculty": [{"meetingTime": []}]},
])
def test_invalid_section_structure_raises_schema_error(overrides):
    with pytest.raises(banner.BannerSchemaError):
        banner._validate_section_schema(section(**overrides))
