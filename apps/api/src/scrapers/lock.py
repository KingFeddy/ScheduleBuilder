from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

logger = logging.getLogger(__name__)

BANNER_SCRAPER_LOCK_ID = 12345001
RMP_SCRAPER_LOCK_ID    = 12345002


@asynccontextmanager
async def advisory_lock(
    session: AsyncSession, lock_id: int, scraper_name: str,
) -> AsyncIterator[bool]:
    """
    Hold a scraper lock on a dedicated connection until this context exits.

    The worker session supplies the engine, never the lock's transaction.
    Its commits/rollbacks cannot release this transaction-level lock. Closing
    the owned connection rolls back the lock transaction on success, failure,
    or cancellation, before returning it to the pool. The worker's pending
    data is untouched. The pool needs capacity for both connections.
    """
    bind = session.bind
    if bind is None:
        raise ValueError("Scraper advisory locks require a bound database engine")
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind

    async with engine.connect() as connection:
        # An AUTOCOMMIT engine would otherwise release the lock at the end of
        # the SELECT. This option applies only to our checked-out connection.
        await connection.execution_options(isolation_level="READ COMMITTED")
        # This transaction deliberately waits during browser/HTTP work. Keep
        # an inherited idle timeout from releasing its lock between requests.
        # SET LOCAL is undone with the transaction when the connection closes.
        await connection.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        if acquired:
            yield True
            return

    # A skipped run may write its health record without reserving a second
    # connection. It never owns or releases the other run's lock.
    logger.info("%s: lock already held — another instance is running", scraper_name)
    yield False
