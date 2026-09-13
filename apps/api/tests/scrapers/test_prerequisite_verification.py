"""Uncertain Banner data must never become a verified empty/partial prerequisite list."""
from __future__ import annotations

import asyncio
import json
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


def row(subject="Fake Test Subject", number="996", **overrides):
    values = dict(connector="", opening="", test="", score="", subject=subject,
                  number=number, level="Undergraduate", grade="C", closing="")
    values.update(overrides)
    return "<tr>" + "".join(f"<td>{value}</td>" for value in values.values()) + "</tr>"


def table(*rows):
    return f'<table class="basePreqTable">{HEAD}<tbody>{"".join(rows)}</tbody></table>'


def response(body, *, status=200, content_type="text/html"):
    return MagicMock(status=status, headers={"content-type": content_type}, text=AsyncMock(return_value=body))


INVALID_HTML = [
    pytest.param("", id="blank"),
    pytest.param("<html><h1>Session expired</h1></html>", id="error-page"),
    pytest.param('{"success": false}', id="json-error"),
    pytest.param("<h3>Catalog Prerequisites</h3>", id="heading-only"),
    pytest.param(EMPTY_HTML.replace("</section>", ""), id="truncated-empty-section"),
    pytest.param(EMPTY_HTML.replace("</section>", "<p>Unable to load</p></section>"), id="error-in-section"),
    pytest.param(EMPTY_HTML.replace("</h3>", "<script>loadPrerequisites()</script></h3>"), id="dynamic-empty-section"),
    pytest.param(EMPTY_HTML.replace("</section>", "<table><tr><td>CS100</td></tr></table></section>"), id="unknown-table"),
    pytest.param(table(), id="empty-table-without-evidence"),
    pytest.param(table(row()).replace("</table>", ""), id="truncated-table"),
    pytest.param(table(row()).replace("</td>", "", 1), id="unclosed-cell"),
    pytest.param(table(row()).replace(HEAD, ""), id="missing-headers"),
    pytest.param(table(row()).replace("<th>Subject</th>", "<th>Department</th>"), id="changed-headers"),
    pytest.param(table(row(), "<tr><td>Missing columns</td></tr>"), id="partial-row"),
    pytest.param(table(row(), row(subject="", connector="And")), id="missing-subject"),
    pytest.param(table(row(), row(number="", connector="And")), id="missing-number"),
    pytest.param(table(row(), row(number="3@", connector="And")), id="unsupported-wildcard"),
    pytest.param(table(row(), row(subject="", number="", test="Placement", score="80", connector="And")), id="test-only-rule"),
    pytest.param(table(row(), row(connector="Or")), id="unsupported-or"),
    pytest.param(table(row(), row()), id="missing-connector"),
    pytest.param(table(row(opening="("), row(closing=")")), id="unsupported-group"),
    pytest.param(table(row()) + table(row()), id="multiple-tables"),
    pytest.param(table(row()).replace("<td>996</td>", '<td colspan="2">996</td>'), id="spanning-cell"),
]


@pytest.mark.parametrize("body", INVALID_HTML)
def test_unrecognized_or_incomplete_html_is_not_an_empty_or_partial_result(body):
    with pytest.raises(RuntimeError):
        prerequisites.parse_prerequisite_table(body)


def test_complete_empty_section_and_complete_course_rows_are_supported():
    assert prerequisites.parse_prerequisite_table(EMPTY_HTML) == []
    assert prerequisites.parse_prerequisite_table(table(row(), row(number="995", connector="And"))) == [
        ("Fake Test Subject", "996"), ("Fake Test Subject", "995"),
    ]


def test_partial_subject_resolution_rejects_the_entire_replacement():
    with pytest.raises(RuntimeError):
        prerequisites.resolve_prerequisite_codes(
            [("Fake Test Subject", "996"), ("Unknown subject", "100")],
            {"Fake Test Subject": SUBJECT}, "ZZZ997",
        )


@pytest.mark.parametrize("entries", [
    None, {}, [], [None], [{}], [{"code": SUBJECT}],
    [{"code": "", "description": "Fake Test Subject"}],
    [{"code": 123, "description": "Fake Test Subject"}],
    [{"code": SUBJECT, "description": "   "}],
    [{"code": SUBJECT, "description": None}],
    [*SUBJECTS, {"code": "ABC", "description": "Fake Test Subject"}],
    [*SUBJECTS, {"code": SUBJECT, "description": "Another subject"}],
])
def test_invalid_or_ambiguous_subject_lookup_is_rejected(entries):
    with pytest.raises(RuntimeError):
        prerequisites.build_subject_lookup(entries)


def test_normalizes_entity_and_whitespace_differences_consistently():
    lookup = prerequisites.build_subject_lookup([
        {"code": "ECE", "description": "Electrical &amp;  Computer\u00a0Engr"},
    ])
    pairs = prerequisites.parse_prerequisite_table(table(row(subject="Electrical &amp; Computer Engr")))
    assert prerequisites.resolve_prerequisite_codes(pairs, lookup, "ZZZ997") == ["ECE996"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    response(json.dumps(SUBJECTS), status=403, content_type="application/json"),
    response(json.dumps(SUBJECTS), content_type="text/html"),
    response("<h1>Session expired</h1>", content_type="application/json"),
    response("null", content_type="application/json"),
    response("[]", content_type="application/json"),
    response(json.dumps([{"code": f"A{i}", "description": f"Subject {i}"} for i in range(100)]), content_type="application/json"),
])
async def test_failed_or_potentially_truncated_lookup_is_rejected(reply):
    page = MagicMock(request=MagicMock(get=AsyncMock(return_value=reply)))
    with pytest.raises(RuntimeError):
        await prerequisites.fetch_subject_lookup(page, "https://example.test/ssb", TERM)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/json", "text/plain", ""])
async def test_prerequisite_endpoint_must_return_html(content_type):
    page = MagicMock(request=MagicMock(post=AsyncMock(return_value=response(EMPTY_HTML, content_type=content_type))))
    with pytest.raises(RuntimeError):
        await prerequisites.fetch_prerequisites(page, "https://example.test/ssb", TERM, "81111")


@pytest.fixture
def source(monkeypatch):
    """Exercise actual lookup, extraction, resolution, and database updates; mock only I/O."""
    cm = _mock_playwright_returning([])
    browser = cm.__aenter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    state = SimpleNamespace(
        lookup=response(json.dumps(SUBJECTS), content_type="application/json"),
        prerequisite=response(table(row())), sections=[section()], browser=browser,
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


FAILURES = [
    pytest.param("lookup", TimeoutError("synthetic timeout"), "failed", id="lookup-timeout"),
    pytest.param("lookup", response(json.dumps(SUBJECTS), status=503, content_type="application/json"), "failed", id="lookup-http-error"),
    pytest.param("lookup", response("[]", content_type="application/json"), "unresolved", id="empty-lookup"),
    pytest.param("prerequisite", TimeoutError("synthetic timeout"), "failed", id="prerequisite-timeout"),
    pytest.param("prerequisite", response("", status=403), "failed", id="prerequisite-http-error"),
    pytest.param("prerequisite", response("<h1>Session expired</h1>"), "unresolved", id="unexpected-html"),
    pytest.param("prerequisite", response(table(row(), row(subject="Unknown subject", connector="And"))), "unresolved", id="partly-resolved"),
    pytest.param("prerequisite", response(table(row(), "<tr><td>Broken</td></tr>")), "unresolved", id="partly-extracted"),
    pytest.param("prerequisite", response(table(row(), row(connector="Or"))), "unresolved", id="unsupported-rule"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute, failure, expected_status", FAILURES)
async def test_failed_refresh_preserves_existing_values_and_records_uncertainty(
    db_session, db_session_factory, source, attribute, failure, expected_status,
):
    await seed(db_session)
    setattr(source, attribute, failure)
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (1, 0, 0)
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
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    verified = await snapshot(db_session_factory)
    assert verified["prerequisites"] == codes
    assert verified["prerequisites_status"] == status
    assert verified["prerequisites_verified_at"] == verified["prerequisites_attempted_at"]
    assert verified["prerequisites_verified_at"] is not None
    assert verified["prerequisites_error"] is None

    source.prerequisite = TimeoutError("synthetic timeout")
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    failed = await snapshot(db_session_factory)
    assert failed["prerequisites"] == codes
    assert failed["prerequisites_verified_at"] == verified["prerequisites_verified_at"]
    assert failed["prerequisites_attempted_at"] > verified["prerequisites_attempted_at"]
    assert failed["prerequisites_status"] == "failed"
    assert failed["prerequisites_error"]

    source.prerequisite = response(body)
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    recovered = await snapshot(db_session_factory)
    assert recovered["prerequisites_status"] == status
    assert recovered["prerequisites_verified_at"] > failed["prerequisites_attempted_at"]
    assert recovered["prerequisites_error"] is None


@pytest.mark.asyncio
async def test_new_course_with_failed_lookup_remains_unverified_empty(db_session, db_session_factory, source):
    source.lookup = TimeoutError("synthetic timeout")
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    saved = await snapshot(db_session_factory)
    assert saved["prerequisites"] == []
    assert saved["prerequisites_status"] == "failed"
    assert saved["prerequisites_verified_at"] is None


@pytest.mark.asyncio
async def test_shared_course_fetches_once_and_other_courses_continue_after_failure(db_session, source):
    source.sections = [section(), section("81112"), section("81113", courseNumber="998")]
    source.prerequisite = TimeoutError("synthetic timeout")
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (3, 0, 0)
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
        await banner.scrape_subject(db_session, SUBJECT, TERM)
    assert await snapshot(db_session_factory) == before
    source.browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_database_replacement_preserves_last_verified_values(db_session, db_session_factory, source):
    await seed(db_session)
    source.prerequisite = response(table(row(number="990"), row(number="991", connector="And")))
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    before = await snapshot(db_session_factory)
    async with db_session.begin():
        # Inject a real database write failure only in this test's private schema.
        await db_session.execute(text("""
            ALTER TABLE courses ADD CONSTRAINT reject_replacement
            CHECK (NOT (prerequisites @> ARRAY['ZZZ996']))
        """))
    source.prerequisite = response(table(row()))
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (1, 0, 0)
    after = await snapshot(db_session_factory)
    assert after["prerequisites"] == before["prerequisites"]
    assert after["prerequisites_verified_at"] == before["prerequisites_verified_at"]
    assert after["prerequisites_status"] == "failed"
    assert after["prerequisites_error"] == "Could not save prerequisite refresh."
    assert after["prerequisites_attempted_at"] > before["prerequisites_attempted_at"]
