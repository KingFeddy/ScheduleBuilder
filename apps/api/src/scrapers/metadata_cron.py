"""Daily prerequisite and professor-rating refresh, independent of section updates."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .banner import run_prerequisite_refresh
from .rmp import run_rmp_scrape
from ..config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_size=3)
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            failed = await run_prerequisite_refresh(session, settings.CURRENT_TERM)
        async with Session() as session:
            await run_rmp_scrape(session=session, term=settings.CURRENT_TERM)
        if failed:
            raise RuntimeError(f"Prerequisite refresh failed for {failed} course(s); see request/persistence errors and course attempt records.")
    finally:
        await engine.dispose()
    logger.info("Metadata refresh complete")


if __name__ == "__main__":
    asyncio.run(main())
