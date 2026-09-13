"""Exercise scraper lock ownership across real PostgreSQL transactions."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from src.scrapers import banner, lock, rmp

pytestmark = pytest.mark.asyncio
TERM = "202690"


async def _can_acquire(session, lock_id):
    try:
        return await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
    finally:
        await session.rollback()


async def _holders(session, lock_id):
    return list(await session.scalars(text("""
        SELECT pid FROM pg_locks
        WHERE locktype = 'advisory' AND granted AND objsubid = 1
          AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND classid = :class_id AND objid = :object_id
    """), {"class_id": (lock_id >> 32) & 0xffffffff,
           "object_id": lock_id & 0xffffffff}))


@pytest.mark.parametrize("boundary", ["commit", "rollback"])
async def test_lock_survives_worker_transaction_boundaries(db_session_factory, boundary):
    async with db_session_factory() as worker, db_session_factory() as other:
        async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
            assert acquired is True
            for _ in range(3):
                await worker.execute(text("SELECT 1"))
                await getattr(worker, boundary)()
                assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is False
        assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is True


async def test_lock_owns_a_connection_separate_from_the_worker(db_session_factory):
    async with db_session_factory() as worker, db_session_factory() as observer:
        worker_pid = await worker.scalar(text("SELECT pg_backend_pid()"))
        async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
            assert acquired is True
            holders = await _holders(observer, lock.BANNER_SCRAPER_LOCK_ID)
            assert len(holders) == 1
            assert holders[0] != worker_pid
        assert await _holders(observer, lock.BANNER_SCRAPER_LOCK_ID) == []


@pytest.mark.parametrize("failure", [None, "application", "database"])
async def test_exit_releases_lock_without_ending_the_worker_transaction(
    db_session, db_session_factory, failure,
):
    async with db_session_factory() as observer:
        await db_session.execute(text("UPDATE courses SET title = 'Pending' WHERE course_code = 'CS999'"))

        async def guarded_work():
            async with lock.advisory_lock(db_session, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
                assert acquired is True
                if failure == "application":
                    raise RuntimeError("Scrape failed")
                if failure == "database":
                    await db_session.execute(text("SELECT 1 / 0"))

        if failure:
            with pytest.raises(RuntimeError if failure == "application" else DBAPIError):
                await guarded_work()
        else:
            await guarded_work()

        assert await _holders(observer, lock.BANNER_SCRAPER_LOCK_ID) == []
        assert db_session.in_transaction()
        assert await observer.scalar(text("SELECT title FROM courses WHERE course_code = 'CS999'")) == "Test Course"
        if failure == "database":
            await db_session.rollback()
        else:
            assert await db_session.scalar(text("SELECT title FROM courses WHERE course_code = 'CS999'")) == "Pending"
            await db_session.commit()
            assert await observer.scalar(text("SELECT title FROM courses WHERE course_code = 'CS999'")) == "Pending"


async def test_same_session_cannot_reenter_but_different_scrapers_can_run(db_session_factory):
    async with db_session_factory() as worker:
        async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "banner") as first:
            assert first is True
            async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "banner") as duplicate:
                assert duplicate is False
            async with lock.advisory_lock(worker, lock.RMP_SCRAPER_LOCK_ID, "rmp") as independent:
                assert independent is True


async def test_failed_acquisition_returns_its_connection(db_session_factory):
    async with db_session_factory() as worker:
        engine = worker.bind
        assert engine.pool.checkedout() == 0
        with pytest.raises(DBAPIError):
            async with lock.advisory_lock(worker, 2**63, "invalid lock id"):
                pytest.fail("An out-of-range PostgreSQL lock ID must fail")
        assert engine.pool.checkedout() == 0


async def test_overlap_returns_contender_connection_before_yielding(db_session_factory):
    async with db_session_factory() as owner, db_session_factory() as contender:
        async with lock.advisory_lock(owner, lock.BANNER_SCRAPER_LOCK_ID, "owner") as acquired:
            assert acquired is True
            assert owner.bind.pool.checkedout() == 1
            async with lock.advisory_lock(contender, lock.BANNER_SCRAPER_LOCK_ID, "contender") as duplicate:
                assert duplicate is False
                assert owner.bind.pool.checkedout() == 1
            assert owner.bind.pool.checkedout() == 1
        assert owner.bind.pool.checkedout() == 0


async def test_autocommit_engine_cannot_release_the_lock_early(db_session_factory):
    engine = db_session_factory.kw["bind"].execution_options(isolation_level="AUTOCOMMIT")
    async with db_session_factory(bind=engine) as worker, db_session_factory() as other:
        async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
            assert acquired is True
            await worker.execute(text("SELECT 1"))
            await worker.commit()
            assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is False
        # The lock's isolation setting must not change the engine's behavior.
        async with engine.connect() as reused:
            assert await reused.scalar(
                text("SELECT pg_try_advisory_xact_lock(:id)"),
                {"id": lock.BANNER_SCRAPER_LOCK_ID},
            ) is True
            assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is True


async def test_idle_timeout_cannot_release_lock_and_is_restored_afterwards(
    test_database_url, db_session_factory,
):
    # Separate pool: a one-connection limit proves the timeout is restored on
    # the actual lock connection when it is reused, not on a fresh connection.
    engine = create_async_engine(
        test_database_url, pool_size=1, max_overflow=0,
        connect_args={"server_settings": {"idle_in_transaction_session_timeout": "100ms"}},
    )
    try:
        async with db_session_factory(bind=engine) as worker, db_session_factory() as other:
            async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
                assert acquired is True
                await other.execute(text("SELECT pg_sleep(0.25)"))
                assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is False
            async with engine.connect() as reused:
                assert await reused.scalar(text("SHOW idle_in_transaction_session_timeout")) == "100ms"
            assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is True
    finally:
        await engine.dispose()


async def test_connection_bound_session_gets_an_independent_lock(db_session_factory):
    engine = db_session_factory.kw["bind"]
    async with engine.connect() as connection, db_session_factory() as other:
        async with db_session_factory(bind=connection) as worker:
            async with lock.advisory_lock(worker, lock.BANNER_SCRAPER_LOCK_ID, "test") as acquired:
                assert acquired is True
                await worker.execute(text("SELECT 1"))
                await worker.commit()
                assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is False
            assert await _can_acquire(other, lock.BANNER_SCRAPER_LOCK_ID) is True


@pytest.mark.parametrize("blocked_subject", [False, True])
async def test_banner_rejects_overlap_after_progress_and_error_commits(
    db_session_factory, monkeypatch, blocked_subject,
):
    async with db_session_factory() as first, db_session_factory() as second:
        calls = []

        async def scrape_subject(session, subject, term):
            calls.append(subject)
            if subject == "DUPLICATE":
                return 0, 0, 0
            # The initial 'running' commit already happened. Also exercise a
            # commit made during a subject and the blocked-subject path.
            await session.execute(text("SELECT 1"))
            await session.commit()
            await banner.run_banner_scrape(second, ["DUPLICATE"], term)
            if subject == "CS" and blocked_subject:
                raise banner.BannerBlockedError("Synthetic block")
            return 1, 0, 0

        monkeypatch.setattr(banner, "scrape_subject", scrape_subject)
        await banner.run_banner_scrape(first, ["CS", "MATH"], TERM)
        assert calls == ["CS", "MATH"]
        assert await second.scalar(text("SELECT count(*) FROM scraper_runs WHERE status = 'skipped_overlap'")) == 2
        assert await _can_acquire(second, lock.BANNER_SCRAPER_LOCK_ID) is True
        # A later trigger is admitted after the first run has finished.
        await banner.run_banner_scrape(first, ["CS"], TERM)
        assert calls == ["CS", "MATH", "CS"]


async def _seed_professor(session):
    await session.execute(text("""
        INSERT INTO sections (crn, term, course_code, section_number, professor_name)
        VALUES ('99999', :term, 'CS999', '001', 'Synthetic Professor')
    """), {"term": TERM})
    await session.commit()


async def test_rmp_rejects_overlap_after_worker_commit(db_session, db_session_factory, monkeypatch):
    await _seed_professor(db_session)
    calls = []
    async with db_session_factory() as other:
        async def fetch_rating(session, name, client):
            calls.append(session)
            if session is other:
                return None
            await session.commit()
            await rmp.run_rmp_scrape(other, TERM)

        monkeypatch.setattr(rmp, "fetch_rmp_rating", fetch_rating)
        await rmp.run_rmp_scrape(db_session, TERM)
        assert calls == [db_session]
        assert await _can_acquire(other, lock.RMP_SCRAPER_LOCK_ID) is True
        await rmp.run_rmp_scrape(other, TERM)
        assert calls == [db_session, other]


@pytest.mark.parametrize("scraper", ["banner", "rmp"])
async def test_cancelled_scraper_releases_lock_and_pool_connections(
    db_session, db_session_factory, monkeypatch, scraper,
):
    await _seed_professor(db_session)
    entered = asyncio.Event()
    lock_id = lock.BANNER_SCRAPER_LOCK_ID if scraper == "banner" else lock.RMP_SCRAPER_LOCK_ID

    async def pause(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(banner, "scrape_subject", pause)
    monkeypatch.setattr(rmp, "fetch_rmp_rating", pause)

    async def run():
        async with db_session_factory() as worker:
            if scraper == "banner":
                await banner.run_banner_scrape(worker, ["CS"], TERM)
            else:
                await rmp.run_rmp_scrape(worker, TERM)

    task = asyncio.create_task(run())
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            async with db_session_factory() as other:
                assert await _can_acquire(other, lock_id) is False
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert db_session.bind.pool.checkedout() == 0
    async with db_session_factory() as other:
        assert await _holders(other, lock_id) == []
        assert await _can_acquire(other, lock_id) is True


@pytest.mark.parametrize("stage,failure", [
    (None, None), ("banner", RuntimeError), ("rmp", RuntimeError),
    ("banner", asyncio.CancelledError), ("rmp", asyncio.CancelledError),
])
async def test_cron_disposes_engine_on_success_failure_and_cancellation(monkeypatch, stage, failure):
    from src.scrapers import cron

    engine = MagicMock(dispose=AsyncMock())
    session_factory = MagicMock()
    banner_run = AsyncMock(side_effect=failure("Synthetic failure") if stage == "banner" else None)
    rmp_run = AsyncMock(side_effect=failure("Synthetic failure") if stage == "rmp" else None)
    monkeypatch.setattr(cron, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(cron, "async_sessionmaker", MagicMock(return_value=session_factory))
    monkeypatch.setattr(cron, "run_banner_scrape", banner_run)
    monkeypatch.setattr(cron, "run_rmp_scrape", rmp_run)

    if failure:
        with pytest.raises(failure):
            await cron.main()
    else:
        await cron.main()

    engine.dispose.assert_awaited_once_with()
    banner_run.assert_awaited_once()
    if stage == "banner":
        rmp_run.assert_not_awaited()
    else:
        rmp_run.assert_awaited_once()
