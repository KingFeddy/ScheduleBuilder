"""Targeted legacy upgrade rehearsed only in disposable private schemas."""
import pytest
import pytest_asyncio
from sqlalchemy import text

from scripts.migrate import load_migrations
from tests.database_isolation import isolated_test_database


@pytest_asyncio.fixture
async def legacy(test_database_url):
    async with isolated_test_database(test_database_url, initialize=False) as db:
        async with db.session_factory.begin() as session:
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            for migration in load_migrations():
                if migration.version in {'000', '007', '009', '012', '013'}:
                    await raw.driver_connection.execute(migration.sql)
            await raw.driver_connection.execute('''
                ALTER TABLE courses ALTER COLUMN credits DROP NOT NULL;
                ALTER TABLE courses ALTER COLUMN prerequisites DROP NOT NULL;
                ALTER TABLE sections ALTER COLUMN total_seats DROP NOT NULL,
                    ALTER COLUMN total_seats DROP DEFAULT, ALTER COLUMN open_seats DROP NOT NULL,
                    ALTER COLUMN open_seats DROP DEFAULT;
                ALTER TABLE sections DROP CONSTRAINT sections_course_code_fkey;
                ALTER TABLE sections ADD CONSTRAINT sections_course_code_fkey FOREIGN KEY(course_code) REFERENCES courses(course_code);
                DROP INDEX idx_sections_course_term;
                DROP INDEX idx_sections_term;
                DROP TABLE professors;
                CREATE TABLE professors(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL,
                    department text NOT NULL, rmp_score numeric, UNIQUE(name, department));
                INSERT INTO professors(name,department,rmp_score) VALUES
                    ('Synthetic Duplicate','CS',4),('Synthetic Duplicate','MATH',3),('Synthetic Unique','CS',5);
                INSERT INTO courses(course_code,title,credits) VALUES ('CS999','Synthetic preserved course',4);
                INSERT INTO sections(crn,term,course_code,total_seats,open_seats) VALUES ('99999','202690','CS999',25,5);
                INSERT INTO meetings(crn,term,days,start_time,end_time,location) VALUES ('99999','202690','M','09:00','10:00','TEST');
            ''')
        yield db


@pytest.mark.asyncio
async def test_inspection_is_read_only_and_reports_ambiguity(legacy):
    from scripts.repair_legacy_catalog import inspect_legacy
    async with legacy.session_factory.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        report = await inspect_legacy(await session.connection(), legacy.schema_name)
        assert report['professor_records'] == 3
        assert report['duplicate_professor_names'] == 1
        assert report['ready']
        assert await session.scalar(text("SELECT to_regclass('professors_legacy_20260913')")) is None


@pytest.mark.asyncio
async def test_upgrade_preserves_legacy_records_and_passes_current_and_future_gates(legacy):
    from scripts.repair_legacy_catalog import repair_legacy
    from scripts.verify_migrations import schema_errors
    from scripts.migrate import apply_migrations
    async with legacy.session_factory.begin() as session:
        before = (await session.execute(text('SELECT to_jsonb(p) FROM professors p ORDER BY name,department'))).scalars().all()
        await repair_legacy(await session.connection(), legacy.schema_name)
        assert (await session.execute(text('SELECT to_jsonb(p) FROM professors_legacy_20260913 p ORDER BY name,department'))).scalars().all() == before
        assert await session.scalar(text('SELECT count(*) FROM professors')) == 2
        assert await session.scalar(text("SELECT department FROM professors WHERE professor_name='Synthetic Duplicate'")) is None
        assert await session.scalar(text('SELECT credits FROM courses')) == 4
        assert await session.scalar(text('SELECT total_seats FROM sections')) == 25
        assert await session.scalar(text('SELECT count(*) FROM meetings')) == 1
        assert await session.scalar(text('SELECT count(*) FROM schema_migrations')) == 9
        assert await schema_errors(await session.connection(), schema_name=legacy.schema_name) == []
        assert await apply_migrations(await session.connection(), schema_name=legacy.schema_name) == []
        assert (await repair_legacy(await session.connection(), legacy.schema_name))['already_compatible']
        assert await session.scalar(text('SELECT count(*) FROM professors_legacy_20260913')) == 3
        assert await session.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid='professors'::regclass")) is True


@pytest.mark.asyncio
async def test_unexpected_data_is_refused_without_changes(legacy):
    from scripts.repair_legacy_catalog import repair_legacy, RepairError
    async with legacy.session_factory.begin() as session:
        await session.execute(text('UPDATE sections SET total_seats=NULL'))
    with pytest.raises(RepairError, match='unknown seat'):
        async with legacy.session_factory.begin() as session:
            await repair_legacy(await session.connection(), legacy.schema_name)
    async with legacy.session_factory() as session:
        assert await session.scalar(text("SELECT to_regclass('professors_legacy_20260913')")) is None
        assert await session.scalar(text('SELECT count(*) FROM professors')) == 3


@pytest.mark.asyncio
async def test_late_verification_failure_rolls_back_the_entire_upgrade(legacy, monkeypatch):
    from scripts import repair_legacy_catalog as repair
    async def failure(*args, **kwargs):
        return ['Synthetic verification failure']
    monkeypatch.setattr(repair, 'schema_errors', failure)
    with pytest.raises(repair.RepairError, match='Synthetic verification'):
        async with legacy.session_factory.begin() as session:
            await repair.repair_legacy(await session.connection(), legacy.schema_name)
    async with legacy.session_factory() as session:
        assert await session.scalar(text("SELECT to_regclass('professors_legacy_20260913')")) is None
        assert await session.scalar(text('SELECT count(*) FROM professors')) == 3
        assert await session.scalar(text("SELECT to_regclass('schema_migrations')")) is None
