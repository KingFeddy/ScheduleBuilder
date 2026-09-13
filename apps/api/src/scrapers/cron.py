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

async def main() -> None:
    # A scraper reserves one connection for its run lock and another for data.
    engine = create_async_engine(settings.DATABASE_URL, pool_size=3)
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        logger.info("Scraper cron starting — term %s; subjects %s", settings.CURRENT_TERM, settings.CATALOG_SUBJECTS)

        async with Session() as session:
            await run_banner_scrape(
                session=session,
                subjects=settings.catalog_subjects,
                term=settings.CURRENT_TERM,
            )

        # RMP runs in a separate session after Banner completes so it sees the
        # full, fresh professor list from the sections table.
        async with Session() as session:
            await run_rmp_scrape(
                session=session,
                term=settings.CURRENT_TERM,
            )
    finally:
        await engine.dispose()
    logger.info("Scraper cron complete")


if __name__ == "__main__":
    asyncio.run(main())
