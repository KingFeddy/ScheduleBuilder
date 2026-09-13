"""Overall run outcomes must describe all requested subjects truthfully."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.scrapers import banner

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("outcomes,expected", [
    ([(5, 0, 0), (2, 0, 1)], "completed"),
    ([(0, 0, 0), (0, 0, 0)], "completed"),
    ([(5, 1, 0)], "partial"),
    ([(0, 2, 0)], "failed"),
    ([(0, 0, 0), banner.BannerBlockedError("Synthetic block")], "partial"),
    ([(5, 0, 0), banner.BannerSchemaError("Synthetic schema change")], "partial"),
    ([banner.BannerBlockedError("Synthetic block")], "failed"),
    ([RuntimeError("Synthetic request failure")], "failed"),
    ([], "failed"),
])
async def test_run_status_and_scope_cover_every_subject(db_session, monkeypatch, outcomes, expected):
    subjects = ["CS", "MATH"][:len(outcomes)]
    monkeypatch.setattr(banner, "scrape_subject", AsyncMock(side_effect=outcomes))
    await banner.run_banner_scrape(db_session, subjects, "202690")
    row = (await db_session.execute(text("SELECT * FROM scraper_runs WHERE subject IS NULL"))).mappings().one()
    assert row["status"] == expected
    assert row["subjects"] == subjects
    assert row["finished_at"] is not None
    if any(isinstance(outcome, Exception) for outcome in outcomes):
        assert row["sections_upserted"] is None  # failed subjects may have committed earlier pages
        assert row["sections_failed"] is None
    else:
        assert row["sections_upserted"] == sum(outcome[0] for outcome in outcomes)
        assert row["sections_failed"] == sum(outcome[1] for outcome in outcomes)


async def test_cancelled_run_is_finished_as_failed_and_cancellation_propagates(db_session, monkeypatch):
    monkeypatch.setattr(banner, "scrape_subject", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await banner.run_banner_scrape(db_session, ["CS"], "202690")
    row = (await db_session.execute(text("SELECT * FROM scraper_runs WHERE subject IS NULL"))).mappings().one()
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["sections_upserted"] is None


async def test_skipped_run_records_scope_and_finish_without_scraping(db_session, monkeypatch):
    @asynccontextmanager
    async def unavailable(*args):
        yield False
    scrape = AsyncMock()
    monkeypatch.setattr(banner, "advisory_lock", unavailable)
    monkeypatch.setattr(banner, "scrape_subject", scrape)
    await banner.run_banner_scrape(db_session, ["CS"], "202690")
    row = (await db_session.execute(text("SELECT * FROM scraper_runs"))).mappings().one()
    assert row["status"] == "skipped_overlap"
    assert row["subjects"] == ["CS"]
    assert row["finished_at"] is not None
    scrape.assert_not_called()


async def test_failed_worker_transaction_does_not_prevent_run_finalization(db_session, monkeypatch):
    async def scrape(session, subject, term, *, progress=None):
        if subject == "CS":
            await session.execute(text("SELECT 1 / 0"))
        return 1, 0, 0
    monkeypatch.setattr(banner, "scrape_subject", scrape)
    await banner.run_banner_scrape(db_session, ["CS", "MATH"], "202690")
    row = (await db_session.execute(text("SELECT * FROM scraper_runs WHERE subject IS NULL"))).mappings().one()
    assert row["status"] == "partial"
    assert row["finished_at"] is not None
    assert row["sections_upserted"] is None
