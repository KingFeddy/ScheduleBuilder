"""Freshness is proof of a complete selected-term refresh, not a recent attempt."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.config import settings

pytestmark = pytest.mark.asyncio
START = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
FINISH = START + timedelta(minutes=10)
TERM = "202690"


async def seed_run(session, **changes):
    values = dict(scraper="banner", subject=None, term=TERM, status="completed",
                  subjects=["CS", "MATH"], started_at=START, finished_at=FINISH,
                  sections_upserted=2, sections_failed=0, error_message=None)
    values.update(changes)
    await session.execute(text("""
        INSERT INTO scraper_runs (scraper, subject, term, status, subjects,
            started_at, finished_at, sections_upserted, sections_failed, error_message)
        VALUES (:scraper, :subject, :term, :status, :subjects,
            :started_at, :finished_at, :sections_upserted, :sections_failed, :error_message)
    """), values)
    await session.commit()


async def seed_section(session, *, crn="99001", term=TERM, scraped_at=FINISH):
    await session.execute(text("""
        INSERT INTO sections (crn, term, course_code, scraped_at)
        VALUES (:crn, :term, 'CS999', :scraped_at)
    """), dict(crn=crn, term=term, scraped_at=scraped_at))
    await session.commit()


@pytest.fixture(autouse=True)
def configured_scope(monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS,MATH")


async def test_latest_attempt_ignores_subject_rows_rmp_and_other_terms(db_session):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session)
    await seed_section(db_session)
    later = START + timedelta(hours=1)
    await seed_run(db_session, status="failed", started_at=later, finished_at=later, sections_failed=1)
    for changes in [dict(subject="CS", status="blocked"), dict(scraper="rmp"), dict(term="202710")]:
        await seed_run(db_session, started_at=later + timedelta(hours=1), **changes)
    result = await load_scraper_status(db_session, TERM)
    assert result.term == TERM
    assert result.status == "failed"
    assert result.latest_attempt.started_at == later
    assert result.last_successful_refresh.finished_at == FINISH
    # Run start bounds the earliest observation; completion time understates age.
    assert result.data_as_of == START


@pytest.mark.parametrize("changes", [
    {'status': 'failed'},
    {'status': 'running'},
    {'status': 'completed', 'subjects': None},
])
async def test_incomplete_or_unverified_attempt_cannot_replace_last_success(db_session, changes):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session)
    later = FINISH + timedelta(minutes=10)
    await seed_run(db_session, **{**dict(started_at=later, finished_at=later + timedelta(minutes=1)), **changes})
    await seed_section(db_session, scraped_at=later)
    result = await load_scraper_status(db_session, TERM)
    assert result.last_successful_refresh.finished_at == FINISH
    assert result.data_as_of == START


async def test_legacy_mixed_success_is_partial_and_unknown_scope_is_not_verified(db_session):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session, subjects=None, sections_failed=1)
    await seed_section(db_session)
    result = await load_scraper_status(db_session, TERM)
    assert result.status == "partial"
    assert result.latest_attempt.status == "partial"
    assert result.last_successful_refresh is None
    assert result.data_as_of is None


@pytest.mark.parametrize("timestamp", [
    None,
    START - timedelta(days=3)
])
async def test_retained_old_or_unknown_section_times_cannot_look_fresh(db_session, timestamp):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session)
    await seed_section(db_session)
    await seed_section(db_session, crn="99002", scraped_at=timestamp)
    # Older sections from other terms must not affect the selected term.
    await seed_section(db_session, term="202710", scraped_at=None)
    result = await load_scraper_status(db_session, TERM)
    assert result.section_count == 2
    assert result.sections_missing_timestamps == (1 if timestamp is None else 0)
    expected = timestamp if timestamp is not None and timestamp < START else None
    assert result.data_as_of == expected


async def test_empty_term_is_never_run_with_unknown_age_and_stable_shape(db_session):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session, term="202710")
    await seed_section(db_session, term="202710")
    result = await load_scraper_status(db_session, TERM)
    assert result.status == "never_run"
    assert result.latest_attempt is None
    assert result.last_successful_refresh is None
    assert result.data_as_of is None
    assert result.section_count == result.sections_missing_timestamps == 0
    assert result.checked_at.tzinfo is not None


async def test_zero_section_success_is_complete_but_does_not_invent_seat_data(db_session):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session, sections_upserted=0)
    result = await load_scraper_status(db_session, TERM)
    assert result.status == "completed"
    assert result.last_successful_refresh.sections_upserted == 0
    assert result.data_as_of is None


async def test_same_start_time_uses_newer_id_and_discovery_is_read_only(db_session):
    from src.services.scraper_status import load_scraper_status
    await seed_run(db_session)
    await seed_run(db_session, status="skipped_overlap")
    async with db_session.begin():
        await db_session.execute(text("SET TRANSACTION READ ONLY"))
        result = await load_scraper_status(db_session, TERM)
    assert result.status == "skipped_overlap"
    assert result.last_successful_refresh.finished_at == FINISH
