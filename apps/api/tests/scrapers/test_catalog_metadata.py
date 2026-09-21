"""Official catalog imports must not depend on sections or erase existing evidence."""
import json

import httpx
import pytest
from sqlalchemy import text

from src.scrapers import catalog_metadata as catalog
from src.scrapers.course_metadata import refresh_course_metadata
from src.services.course_metadata import course_response

URL = "https://catalog.njit.edu/undergraduate/science-liberal-arts/physics/"


def page(*headings, year=2026):
    return f'<h1>University Catalog {year}-{year + 1}</h1>' + ''.join(
        f'<div class="courseblock"><p class="courseblocktitle"><strong>{heading}</strong></p>'
        '<p class="courseblockdesc">Prerequisite: approval. Not imported.</p></div>'
        for heading in headings
    )


def parse(*headings, year=2026):
    return catalog.parse_catalog_page(page(*headings, year=year), url=URL, subject="PHYS", catalog_year=year)


def test_catalog_only_course_with_source_evidence_and_subject_filter():
    result = parse("MTSE 301. Materials. 3 credits.",
                   "PHYS\u00a0485. Computer Modeling of Applied Physics Problems. 3 credits, 3 contact hours (3;0;0).",
                   "R750 485. Individual Research. 1-3 credits.")
    assert len(result.courses) == 1
    record = result.courses[0]
    assert record.course_code == "PHYS485"
    assert record.title == "Computer Modeling of Applied Physics Problems"
    assert record.credits == {"kind": "fixed", "minimum": 3, "maximum": 3}
    evidence = result.evidence(record, record.title)
    assert evidence["catalog_year"] == 2026 and evidence["url"] == URL
    assert evidence["source_kind"] == "njit_catalog" and evidence["observed_at"]
    assert evidence["heading"].startswith("PHYS 485.")


@pytest.mark.parametrize("credit_text,kind,low,high", [
    ("0.5", "fixed", .5, .5),
    ("1-3", "range", 1, 3),
    ("1 or 3", "options", 1, 3)
])
def test_credit_shapes(credit_text, kind, low, high):
    record = parse(f"PHYS 491. Independent Study II. {credit_text} credits.").courses[0]
    assert record.credits == {"kind": kind, "minimum": low, "maximum": high}


@pytest.mark.parametrize("bad", [
    "PHYS 485. Modeling. 3-1 credits.",
    "PHYS 485. TBD. 3 credits.",
    "PHYS 485. Modeling. 3 credits. unsupported tail"
])
def test_malformed_relevant_record_aborts_entire_page(bad):
    with pytest.raises(ValueError):
        parse("PHYS 491. Research. 3 credits.", bad)


def test_missing_subject_or_wrong_catalog_edition_rejects():
    with pytest.raises(ValueError):
        parse("MTSE 301. Materials. 3 credits.")
    with pytest.raises(ValueError):
        catalog.parse_catalog_page(page("PHYS 485. Modeling. 3 credits.", year=2025),
                                   url=URL, subject="PHYS", catalog_year=2026)


def test_duplicates_only_deduplicate_identical_facts():
    heading = "PHYS 485. Modeling. 3 credits."
    assert len(parse(heading, heading).courses) == 1
    with pytest.raises(ValueError):
        parse(heading, "PHYS 485. Another title. 3 credits.")


@pytest.mark.parametrize("url", [
    "https://example.com/undergraduate/physics/",
    "https://catalog.njit.edu@evil.example/undergraduate/physics/",
    "https://catalog.njit.edu/graduate/physics/"
])
def test_url_scope_is_explicit(url):
    with pytest.raises(ValueError):
        catalog.validate_catalog_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,body", [
    (302, {"location": "https://example.com/"}, b""),
    (200, {"content-type": "application/json"}, b"{}"),
    (200, {"content-type": "text/html"}, b"x" * (catalog.MAX_PAGE_BYTES + 1)),
])
async def test_fetch_rejects_redirects_non_html_and_oversized_pages(status, headers, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, headers=headers, content=body)
    )) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            await catalog.fetch_catalog_page(URL, client=client)


async def row(session, code="PHYS485"):
    result = (await session.execute(text("SELECT * FROM courses WHERE course_code=:code"), {"code": code})).mappings().one()
    await session.rollback()
    return result


@pytest.mark.asyncio
async def test_import_creates_catalog_only_course_without_prerequisite_claims(db_session):
    await catalog.import_catalog_page(db_session, parse("PHYS 485. Modeling. 3 credits."))
    saved = await row(db_session)
    assert saved["title"] == "Modeling" and saved["credits"] == 3
    assert saved["prerequisites"] == [] and saved["prerequisites_status"] == "unverified"
    assert saved["prerequisites_source"] is None
    source = json.loads(saved["title_source"]) if isinstance(saved["title_source"], str) else saved["title_source"]
    assert source["source_kind"] == "njit_catalog"
    assert course_response(saved).title_status == "verified"


@pytest.mark.asyncio
async def test_catalog_title_survives_banner_and_banner_credits_survive_catalog(db_session):
    await db_session.execute(text("""INSERT INTO courses(course_code,title,credits,prerequisites)
                                 VALUES ('PHYS485','Old topic',3,ARRAY['PHYS234'])"""))
    await db_session.commit()
    await refresh_course_metadata(db_session, "PHYS485", "202690", [
        {"courseReferenceNumber": "12345", "courseTitle": "Section topic", "creditHours": 4}
    ], lambda value: value, source_url="https://banner.example/search")
    await catalog.import_catalog_page(db_session, parse("PHYS 485. Canonical title. 3 credits."))
    saved = await row(db_session)
    assert saved["title"] == "Canonical title" and saved["credits"] == 4
    assert saved["prerequisites"] == ["PHYS234"]
    await refresh_course_metadata(db_session, "PHYS485", "202690", [
        {"courseReferenceNumber": "12345", "courseTitle": "Another topic", "creditHours": 2}
    ], lambda value: value, source_url="https://banner.example/search")
    saved = await row(db_session)
    assert saved["title"] == "Canonical title" and saved["credits"] == 2


@pytest.mark.asyncio
async def test_old_edition_cannot_replace_newer_catalog_but_newer_variable_credits_can(db_session):
    await catalog.import_catalog_page(db_session, parse("PHYS 485. Current title. 3 credits."))
    await catalog.import_catalog_page(db_session, parse("PHYS 485. Old title. 1 credits.", year=2025))
    saved = await row(db_session)
    assert saved["title"] == "Current title" and saved["credits"] == 3
    await catalog.import_catalog_page(db_session, parse("PHYS 485. New title. 1-4 credits.", year=2027))
    saved = await row(db_session)
    assert saved["title"] == "New title" and saved["credits"] is None
    assert course_response(saved).credits_status == "variable"


@pytest.mark.asyncio
async def test_failed_import_rolls_back_whole_page(db_session):
    await db_session.execute(text("ALTER TABLE courses ADD CONSTRAINT reject_one CHECK (credits <> 1)"))
    await db_session.commit()
    with pytest.raises(Exception):
        await catalog.import_catalog_page(db_session, parse("PHYS 485. Good row. 3 credits.", "PHYS 491. Bad row. 1 credits."))
    assert await db_session.scalar(text("SELECT count(*) FROM courses WHERE course_code LIKE 'PHYS%'")) == 0


@pytest.mark.asyncio
async def test_import_refuses_banner_lock_contention(db_session, isolated_database):
    from src.scrapers import lock
    async with lock.advisory_lock(db_session, lock.BANNER_SCRAPER_LOCK_ID, "test-banner") as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match="already running"):
            await catalog.import_catalog_page(db_session, parse("PHYS 485. Modeling. 3 credits."))


@pytest.mark.asyncio
async def test_cli_preview_never_opens_database(monkeypatch, capsys):
    from argparse import Namespace
    from unittest.mock import AsyncMock, Mock
    from scripts import import_catalog_metadata as command
    monkeypatch.setenv("DATABASE_URL", "intentionally-invalid-preview-does-not-read-it")
    monkeypatch.setattr(command, "fetch_catalog_page", AsyncMock(return_value=page("PHYS 485. Modeling. 3 credits.")))
    database = Mock(side_effect=AssertionError("Preview must not access a database"))
    monkeypatch.setattr(command, "create_async_engine", database)
    assert await command.run(Namespace(url=URL, subject="PHYS", catalog_year=2026, apply=False)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["database_accessed"] is False and output["candidate_count"] == 1
    assert not database.called


@pytest.mark.asyncio
async def test_cli_schema_gate_prevents_import(monkeypatch):
    from argparse import Namespace
    from unittest.mock import AsyncMock, Mock
    from scripts import import_catalog_metadata as command, verify_migrations
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")
    monkeypatch.setattr(command, "fetch_catalog_page", AsyncMock(return_value=page("PHYS 485. Modeling. 3 credits.")))
    monkeypatch.setattr(verify_migrations, "verify", AsyncMock(return_value=False))
    database = Mock(side_effect=AssertionError("Failed schema gate must not open writer engine"))
    monkeypatch.setattr(command, "create_async_engine", database)
    assert await command.run(Namespace(url=URL, subject="PHYS", catalog_year=2026, apply=True)) == 1
    assert not database.called


@pytest.mark.asyncio
async def test_cli_rejected_page_explains_edition_and_never_writes(monkeypatch, capsys):
    from argparse import Namespace
    from unittest.mock import AsyncMock
    from scripts import import_catalog_metadata as command
    monkeypatch.setattr(command, "fetch_catalog_page", AsyncMock(return_value=page("PHYS 485. Modeling. 3 credits.", year=2025)))
    writer = AsyncMock()
    monkeypatch.setattr(command, "import_catalog_page", writer)
    assert await command.run(Namespace(url=URL, subject="PHYS", catalog_year=2026, apply=True)) == 1
    assert "edition does not match" in capsys.readouterr().err
    writer.assert_not_called()


@pytest.mark.parametrize("subject,placeholder,number", [("COM", "1**", "312")])
def test_elective_placeholders_are_reported_without_blocking_real_courses(subject, placeholder, number):
    heading = f"{subject}\u00a0{placeholder}. Synthetic Elective. 3 credits, 3 contact hours (3;0;0)."
    result = catalog.parse_catalog_page(page(heading, f"{subject} {number}. Synthetic Course. 3 credits."),
                                        url=URL, subject=subject, catalog_year=2026)
    assert [record.course_code for record in result.courses] == [subject + number]
    assert result.skipped_placeholders == (heading.replace('\u00a0', ' '),)


@pytest.mark.parametrize("bad", [
    "CS 49*. Damaged code. 3 credits.",
    "CS 4**. Elective. Unknown credits.",
    "CS 491. Real course. Unknown credits."
])
def test_placeholder_support_does_not_ignore_malformed_entries(bad):
    with pytest.raises(ValueError):
        catalog.parse_catalog_page(page("CS 490. Valid Course. 3 credits.", bad),
                                   url=URL, subject="CS", catalog_year=2026)


def test_placeholder_only_page_is_not_a_successful_import():
    with pytest.raises(ValueError, match="No supported CS courses"):
        catalog.parse_catalog_page(page("CS 2**. Synthetic Elective. 3 credits."),
                                   url=URL, subject="CS", catalog_year=2026)


@pytest.mark.asyncio
async def test_cli_preview_reports_skipped_placeholders(monkeypatch, capsys):
    from argparse import Namespace
    from unittest.mock import AsyncMock
    from scripts import import_catalog_metadata as command
    heading = "COM 1**. Synthetic Elective. 3 credits."
    monkeypatch.setattr(command, "fetch_catalog_page", AsyncMock(return_value=page(
        heading, "COM 312. Synthetic Communication. 3 credits.")))
    assert await command.run(Namespace(url=URL, subject="COM", catalog_year=2026, apply=False)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidate_count"] == 1 and output["skipped_placeholder_count"] == 1
    assert output["skipped_placeholders"] == [heading]
    assert [record["course_code"] for record in output["courses"]] == ["COM312"]
