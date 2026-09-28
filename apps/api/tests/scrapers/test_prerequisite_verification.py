"""Uncertain Banner data must never become a verified empty/partial prerequisite list."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from src.scrapers import banner, prerequisites
from tests.scrapers.test_banner_resilience import _mock_playwright_returning
from tests.scrapers.test_banner_responses import TERM, SUBJECT, results, section


EMPTY_HTML = '<section aria-labelledby="preReqs"><h3>Catalog Prerequisites</h3></section>'
HEAD = "<thead><tr>" + "".join(
    f"<th>{label}</th>" for label in
    ["And/Or", "", "Test", "Score", "Subject", "Course Number", "Level", "Grade", ""]
) + "</tr></thead>"
SUBJECTS = [{"code": SUBJECT, "description": "Fake Test Subject"}]
EMPTY_COREQUISITES = '<table class="basePreqTable"><thead><tr><th>Subject</th><th>Course Number</th><th>Title</th></tr></thead><tbody></tbody></table>'


def row(subject="Fake Test Subject", number="996", **overrides):
    values = dict(connector="", opening="", test="", score="", subject=subject,
                  number=number, level="Undergraduate", grade="C", closing="")
    values.update(overrides)
    return "<tr>" + "".join(f"<td>{value}</td>" for value in values.values()) + "</tr>"


def table(*rows):
    return f'<table class="basePreqTable">{HEAD}<tbody>{"".join(rows)}</tbody></table>'


def response(body, *, status=200, content_type="text/html"):
    return MagicMock(status=status, headers={"content-type": content_type}, text=AsyncMock(return_value=body))


# Public Banner responses captured for Fall 2026 on 2026-09-27; no database I/O.
BANNER_FIXTURES = Path(__file__).parent / "fixtures" / "banner"


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture,crn,status", [
    ("empty", "90001", "verified_empty"),
    ("acct215", "90010", "verified"),
    ("arch295", "90083", "unresolved"),
])
async def test_captured_banner_empty_messages_do_not_block_valid_prerequisites(fixture, crn, status):
    from src.schemas.prerequisites import AnyConditions, is_empty

    prerequisite_html = (BANNER_FIXTURES / f"{fixture}-prerequisites.html").read_text()
    corequisite_html = (BANNER_FIXTURES / "empty-corequisites.html").read_text()
    subjects = [
        {"code": "ACCT", "description": "Accounting"},
        {"code": "ARCH", "description": "Architecture"},
    ]
    page = MagicMock(request=MagicMock(
        get=AsyncMock(return_value=response(json.dumps(subjects), content_type="application/json")),
        post=AsyncMock(side_effect=[response(prerequisite_html), response(corequisite_html)]),
    ))
    lookup = await prerequisites.fetch_subject_lookup(page, "https://example.test/ssb", "202690")
    result = await prerequisites.fetch_prerequisites(page, "https://example.test/ssb", "202690", crn, lookup)

    assert result.status == status
    assert is_empty(result.rules.corequisites)
    assert [source.text for source in result.sources[1:]] == [prerequisite_html, corequisite_html]
    if status == "verified":
        assert result.error is None
        assert isinstance(result.rules.prerequisites, AnyConditions)
        assert [(rule.course_code, rule.minimum_grade) for rule in result.rules.prerequisites.items] == [
            ("ACCT115", "D"), ("ACCT117", "D"),
        ]
    elif status == "verified_empty":
        assert result.error is None
        assert is_empty(result.rules.prerequisites)
    else:
        assert "Mixed AND/OR" in result.error


@pytest.mark.parametrize("replacement", [
    "No prerequisite information available. Advisor approval required.",
    "No prerequisite information available.<p>CS100 required</p>",
    "No corequisite course information available.",
])
def test_empty_message_must_not_hide_unknown_conditions_or_wrong_response(replacement):
    body = (BANNER_FIXTURES / "empty-prerequisites.html").read_text()
    body = body.replace("No prerequisite information available.", replacement)
    with pytest.raises(prerequisites.PrerequisiteDataError):
        prerequisites.parse_prerequisite_rules(body, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses,lookup_error,expected_failed,summary", [
    (["verified", "verified_empty", "unresolved"], None, 0,
     "3 courses, 2 verified, 1 unresolved, 0 failed"),
    (["unresolved", "failed", RuntimeError("Synthetic write failure")], None, 2,
     "3 courses, 0 verified, 1 unresolved, 2 failed"),
    (["unresolved"], prerequisites.PrerequisiteDataError("Invalid subject lookup"), 1,
     "1 courses, 0 verified, 0 unresolved, 1 failed"),
])
async def test_metadata_refresh_separates_unresolved_rules_from_operational_failures(
    monkeypatch, caplog, statuses, lookup_error, expected_failed, summary,
):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def acquired_lock(*args):
        yield True

    session = MagicMock(execute=AsyncMock(return_value=MagicMock()), rollback=AsyncMock())
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"course_code": f"ZZZ{100 + i}", "crn": str(90000 + i)}
        for i in range(len(statuses))
    ]
    cm = _mock_playwright_returning([])
    browser = cm.__aenter__.return_value.chromium.launch.return_value
    refresh = AsyncMock(side_effect=statuses)
    monkeypatch.setattr(banner, "advisory_lock", acquired_lock)
    monkeypatch.setattr(banner, "async_playwright", MagicMock(return_value=cm))
    monkeypatch.setattr(banner, "_open_banner_term", AsyncMock())
    monkeypatch.setattr(banner, "_prerequisite_lookup", AsyncMock(return_value=(None, lookup_error)))
    monkeypatch.setattr(banner, "_refresh_course_prerequisites", refresh)

    assert await banner.run_prerequisite_refresh(session, TERM) == expected_failed
    assert refresh.await_count == len(statuses)
    assert summary in caplog.text
    browser.close.assert_awaited_once()
    assert session.rollback.await_count == sum(isinstance(s, Exception) for s in statuses)


INVALID_HTML = [
    pytest.param("", id="blank"),
    pytest.param("<h3>Catalog Prerequisites</h3>", id="heading-only"),
    pytest.param(EMPTY_HTML.replace("</section>", "<table><tr><td>CS100</td></tr></table></section>"), id="unknown-table")
]


@pytest.mark.parametrize("body", INVALID_HTML)
def test_unrecognized_or_incomplete_html_is_not_an_empty_or_partial_result(body):
    with pytest.raises(RuntimeError):
        prerequisites.parse_prerequisite_rules(body, {"Fake Test Subject": SUBJECT})


def test_complete_empty_section_and_complete_course_rows_are_supported():
    from src.schemas.prerequisites import is_empty
    assert is_empty(prerequisites.parse_prerequisite_rules(EMPTY_HTML, {"Fake Test Subject": SUBJECT}))
    rules = prerequisites.parse_prerequisite_rules(table(row(), row(number="995", connector="And")), {"Fake Test Subject": SUBJECT})
    assert [rule.course_code for rule in rules.items] == ["ZZZ996", "ZZZ995"]


def test_partial_subject_resolution_rejects_the_entire_replacement():
    from src.schemas.prerequisites import has_unresolved
    rule = prerequisites.parse_prerequisite_rules(
        table(row(), row(subject="Unknown subject", number="100", connector="And")),
        {"Fake Test Subject": SUBJECT},
    )
    assert has_unresolved(rule)
    assert rule.items[1].reason == "unresolved_subject"


@pytest.mark.parametrize("entries", [
    None,
    [{"code": SUBJECT}]
])
def test_invalid_or_ambiguous_subject_lookup_is_rejected(entries):
    with pytest.raises(RuntimeError):
        prerequisites.build_subject_lookup(entries)


def test_normalizes_entity_and_whitespace_differences_consistently():
    lookup = prerequisites.build_subject_lookup([
        {"code": "ECE", "description": "Electrical &amp;  Computer\u00a0Engr"},
    ])
    rule = prerequisites.parse_prerequisite_rules(table(row(subject="Electrical &amp; Computer Engr")), lookup)
    assert rule.course_code == "ECE996"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    response(json.dumps(SUBJECTS), status=403, content_type="application/json"),
    response("<h1>Session expired</h1>", content_type="application/json")
])
async def test_failed_or_potentially_truncated_lookup_is_rejected(reply):
    page = MagicMock(request=MagicMock(get=AsyncMock(return_value=reply)))
    with pytest.raises(RuntimeError):
        await prerequisites.fetch_subject_lookup(page, "https://example.test/ssb", TERM)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/json"])
async def test_prerequisite_endpoint_must_return_html(content_type):
    page = MagicMock(request=MagicMock(post=AsyncMock(return_value=response(EMPTY_HTML, content_type=content_type))))
    with pytest.raises(RuntimeError):
        await prerequisites.fetch_rule_source(page, "https://example.test/ssb", TERM, "81111", "prerequisites")


@pytest.fixture
def source(monkeypatch):
    """Exercise actual lookup, extraction, resolution, and database updates; mock only I/O."""
    cm = _mock_playwright_returning([])
    browser = cm.__aenter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    state = SimpleNamespace(
        lookup=response(json.dumps(SUBJECTS), content_type="application/json"),
        prerequisite=response(table(row())), corequisite=response(EMPTY_COREQUISITES),
        sections=[section()], browser=browser,
        prerequisite_requests=[],
    )

    async def get(url, **kwargs):
        assert "/classSearch/get_subject?" in url
        if isinstance(state.lookup, BaseException):
            raise state.lookup
        return state.lookup

    async def post(url, **kwargs):
        if "/term/search?" in url:
            return response('{"fwdURL": ""}', content_type="application/json")
        if url.endswith("/searchResults/getCorequisites"):
            if isinstance(state.corequisite, BaseException):
                raise state.corequisite
            return state.corequisite
        assert url.endswith("/searchResults/getSectionPrerequisites")
        state.prerequisite_requests.append(kwargs["form"])
        if isinstance(state.prerequisite, BaseException):
            raise state.prerequisite
        return state.prerequisite

    async def fetch_page(*args, **kwargs):
        return results(state.sections, len(state.sections))

    page.request.get = AsyncMock(side_effect=get)
    page.request.post = AsyncMock(side_effect=post)
    monkeypatch.setattr(banner, "async_playwright", MagicMock(return_value=cm))
    monkeypatch.setattr(banner, "_fetch_page", fetch_page)
    return state


async def seed(session):
    async with session.begin():
        await session.execute(text("""
            INSERT INTO courses (course_code, title, credits, prerequisites)
            VALUES ('ZZZ997', 'Preserve this title', 4, ARRAY['ZZZ990', 'ZZZ991'])
        """))


async def snapshot(factory):
    async with factory() as observer:
        return dict((await observer.execute(text("SELECT * FROM courses WHERE course_code = 'ZZZ997'"))).mappings().one())


@pytest.mark.asyncio
async def test_sections_skip_prerequisites_and_metadata_preserves_section_freshness(
    db_session, db_session_factory, source,
):
    await seed(db_session)
    source.sections = [section(), section("81112")]
    before = await snapshot(db_session_factory)
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (2, 0, 0)
    assert source.prerequisite_requests == []
    source.browser.new_context.return_value.new_page.return_value.request.get.assert_not_awaited()
    after = await snapshot(db_session_factory)
    assert {k: v for k, v in after.items() if k.startswith('prerequisites')} == {
        k: v for k, v in before.items() if k.startswith('prerequisites')
    }

    async def sections():
        async with db_session_factory() as observer:
            return (await observer.execute(text("SELECT * FROM sections ORDER BY crn, term"))).mappings().all()

    async with db_session.begin():
        await db_session.execute(text("""
            INSERT INTO sections(crn, term, course_code, section_number) VALUES
                ('80000', :term, 'ZZZ997', ' fp '), ('70000', '202610', 'ZZZ997', '001')
        """), {'term': TERM})
    section_snapshot = await sections()
    assert await banner.run_prerequisite_refresh(db_session, TERM) == 0
    # Skip placeholders and other semesters; fetch only once for a shared course.
    assert source.prerequisite_requests == [{"term": TERM, "courseReferenceNumber": "81111"}]
    verified = await snapshot(db_session_factory)
    assert verified['prerequisites'] == ['ZZZ996']
    assert verified['prerequisites_status'] == 'verified'
    source.prerequisite = TimeoutError('Synthetic timeout')
    assert await banner.run_prerequisite_refresh(db_session, TERM) == 1
    failed = await snapshot(db_session_factory)
    assert failed['prerequisites'] == verified['prerequisites']
    assert failed['prerequisites_verified_at'] == verified['prerequisites_verified_at']
    assert failed['prerequisites_status'] == 'failed'
    assert await sections() == section_snapshot


FAILURES = [
    pytest.param("lookup", TimeoutError("synthetic timeout"), "failed", id="lookup-timeout"),
    pytest.param("lookup", response("[]", content_type="application/json"), "unresolved", id="empty-lookup")
]


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute, failure, expected_status", FAILURES)
async def test_failed_refresh_preserves_existing_values_and_records_uncertainty(
    db_session, db_session_factory, source, attribute, failure, expected_status,
):
    await seed(db_session)
    setattr(source, attribute, failure)
    assert await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True) == (1, 0, 0)
    saved = await snapshot(db_session_factory)
    assert saved["prerequisites"] == ["ZZZ990", "ZZZ991"]
    assert (saved["title"], saved["credits"]) == ("Preserve this title", 4)
    assert saved["prerequisites_status"] == expected_status
    assert saved["prerequisites_attempted_at"] is not None
    assert saved["prerequisites_verified_at"] is None
    assert saved["prerequisites_error"]
    if attribute == "lookup":
        assert source.prerequisite_requests == []
    source.browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("body, codes, status", [
    (table(row(), row(number="995", connector="And")), ["ZZZ996", "ZZZ995"], "verified"),
    (EMPTY_HTML, [], "verified_empty"),
])
async def test_verified_refresh_then_failure_then_recovery(
    db_session, db_session_factory, source, body, codes, status,
):
    await seed(db_session)
    source.prerequisite = response(body)
    await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    verified = await snapshot(db_session_factory)
    assert verified["prerequisites"] == codes
    assert verified["prerequisites_status"] == status
    assert verified["prerequisites_verified_at"] == verified["prerequisites_attempted_at"]
    assert verified["prerequisites_verified_at"] is not None
    assert verified["prerequisites_error"] is None

    source.prerequisite = TimeoutError("synthetic timeout")
    await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    failed = await snapshot(db_session_factory)
    assert failed["prerequisites"] == codes
    assert failed["prerequisites_verified_at"] == verified["prerequisites_verified_at"]
    assert failed["prerequisites_attempted_at"] > verified["prerequisites_attempted_at"]
    assert failed["prerequisites_status"] == "failed"
    assert failed["prerequisites_error"]

    source.prerequisite = response(body)
    await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    recovered = await snapshot(db_session_factory)
    assert recovered["prerequisites_status"] == status
    assert recovered["prerequisites_verified_at"] > failed["prerequisites_attempted_at"]
    assert recovered["prerequisites_error"] is None


@pytest.mark.asyncio
async def test_new_course_with_failed_lookup_remains_unverified_empty(db_session, db_session_factory, source):
    source.lookup = TimeoutError("synthetic timeout")
    await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    saved = await snapshot(db_session_factory)
    assert saved["prerequisites"] == []
    assert saved["prerequisites_status"] == "failed"
    assert saved["prerequisites_verified_at"] is None


@pytest.mark.asyncio
async def test_shared_course_fetches_once_and_other_courses_continue_after_failure(db_session, source):
    source.sections = [section(), section("81112"), section("81113", courseNumber="998")]
    source.prerequisite = TimeoutError("synthetic timeout")
    assert await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True) == (3, 0, 0)
    assert source.prerequisite_requests == [
        {"term": TERM, "courseReferenceNumber": "81111"},
        {"term": TERM, "courseReferenceNumber": "81113"},
    ]


@pytest.mark.asyncio
async def test_cancellation_preserves_prerequisites_and_propagates(db_session, db_session_factory, source):
    await seed(db_session)
    before = await snapshot(db_session_factory)
    source.prerequisite = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    assert await snapshot(db_session_factory) == before
    source.browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_database_replacement_preserves_last_verified_values(db_session, db_session_factory, source):
    await seed(db_session)
    source.prerequisite = response(table(row(number="990"), row(number="991", connector="And")))
    await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True)
    before = await snapshot(db_session_factory)
    async with db_session.begin():
        # Inject a real database write failure only in this test's private schema.
        await db_session.execute(text("""
            ALTER TABLE courses ADD CONSTRAINT reject_replacement
            CHECK (NOT (prerequisites @> ARRAY['ZZZ996']))
        """))
    source.prerequisite = response(table(row()))
    assert await banner.scrape_subject(db_session, SUBJECT, TERM, refresh_prerequisites=True) == (1, 0, 0)
    after = await snapshot(db_session_factory)
    assert after["prerequisites"] == before["prerequisites"]
    assert after["prerequisites_verified_at"] == before["prerequisites_verified_at"]
    assert after["prerequisites_status"] == "failed"
    assert after["prerequisites_error"] == "Could not save prerequisite refresh."
    assert after["prerequisites_attempted_at"] > before["prerequisites_attempted_at"]
