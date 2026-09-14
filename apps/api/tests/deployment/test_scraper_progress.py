"""A late subject error must preserve evidence of already committed progress."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError, asyncio.CancelledError])
async def test_failure_after_committed_sections_is_partial(monkeypatch, error):
    from src.scrapers import banner

    @asynccontextmanager
    async def context(*args):
        yield True

    session = MagicMock()
    session.begin = context
    session.execute = AsyncMock()
    session.execute.return_value.scalar_one = MagicMock(return_value=1)
    session.rollback = AsyncMock()
    finish = AsyncMock()

    async def scrape(session, subject, term, *, progress):
        progress.sections_upserted = 1
        raise error("Synthetic interruption after a committed section")

    monkeypatch.setattr(banner, "advisory_lock", context)
    monkeypatch.setattr(banner, "scrape_subject", scrape)
    monkeypatch.setattr(banner, "_finish_banner_run", finish)
    if error is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await banner.run_banner_scrape(session, ["CS"], "202690")
    else:
        await banner.run_banner_scrape(session, ["CS"], "202690")
    assert finish.await_args.args[2] == "partial"
    assert finish.await_args.args[3] is None  # the full run's totals remain unknown
