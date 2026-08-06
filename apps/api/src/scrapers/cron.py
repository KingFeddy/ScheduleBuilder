"""
Scraper cron entry point — runs as a standalone Railway service.

Invoked by Railway on schedule: */30 * * * *
Not a FastAPI server — creates its own engine rather than using app.state.
Banner runs first; RMP queries the sections table for professor names, so it
must see the fresh Banner data before running.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .banner import run_banner_scrape
from .rmp import run_rmp_scrape
from ..config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Every subject with at least one course in the `courses` catalog — kept in
# sync with `SELECT DISTINCT SUBSTRING(course_code FROM '^[A-Z]+') FROM courses`.
# The original list only covered 12 of these; the other 6 (ACCT, FIN, FRSC,
# IT, SDET, YWCC) were searchable via /api/courses but their sections were
# never scraped, so seat counts/meeting times for them went stale forever
# and the new stale-section cleanup in banner.py never got a chance to run
# for them either, since that only fires for subjects actually scraped.
SUBJECTS = [
    "CS", "MATH", "PHYS", "ECE", "CHEM",
    "COM", "HIST", "LIT", "PHIL", "PSY", "STS", "THTR",
    "ACCT", "FIN", "FRSC", "IT", "SDET", "YWCC",
]


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_size=3)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    logger.info("Scraper cron starting — term %s", settings.CURRENT_TERM)

    async with Session() as session:
        await run_banner_scrape(
            session=session,
            subjects=SUBJECTS,
            term=settings.CURRENT_TERM,
        )

    # RMP runs in a separate session after Banner completes so it sees the
    # full, fresh professor list from the sections table.
    async with Session() as session:
        await run_rmp_scrape(
            session=session,
            term=settings.CURRENT_TERM,
        )

    await engine.dispose()
    logger.info("Scraper cron complete")


if __name__ == "__main__":
    asyncio.run(main())
