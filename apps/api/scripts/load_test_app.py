"""Local load-test factory. Never use this entry point for deployment."""
from contextlib import asynccontextmanager
import os


def create_app():
    from tests.database import (
        configure_test_environment, load_test_database_config, verify_test_database,
    )

    config = load_test_database_config(os.environ)
    token = os.environ.get("LOAD_TEST_TOKEN")
    if config is None or not token:
        raise RuntimeError("Load testing requires a guarded disposable database and run token.")
    configure_test_environment(os.environ, config)

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from tests.database_isolation import isolated_test_database
    from main import app, limiter

    @asynccontextmanager
    async def lifespan(application):
        await verify_test_database(config)
        async with isolated_test_database(config.url) as database:
            async with database.session_factory.begin() as db:
                await db.execute(text("""
                    INSERT INTO courses(course_code, title, credits)
                    SELECT subject || number, 'Synthetic course ' || subject || number, 3
                    FROM unnest(ARRAY['CS', 'MATH', 'PHYS', 'IT']) subject,
                         generate_series(100, 399) number
                """))
                await db.execute(text("""
                    INSERT INTO sections(crn, term, course_code, section_number,
                                         professor_name, total_seats, open_seats, scraped_at)
                    SELECT course_code || '-' || n, '202690', course_code, '00' || n,
                           'Test Professor ' || n, 30, CASE WHEN n = 5 THEN 0 ELSE 10 END, now()
                    FROM courses CROSS JOIN generate_series(1, 5) n
                """))
                await db.execute(text("""
                    INSERT INTO meetings(crn, term, days, start_time, end_time, location)
                    SELECT crn, term, CASE WHEN section_number IN ('002', '004') THEN 'TR' ELSE 'MW' END,
                           make_time(8 + right(course_code, 3)::int % 6, 0, 0),
                           make_time(8 + right(course_code, 3)::int % 6, 50, 0), 'Test room'
                    FROM sections
                """))
                await db.execute(text("""
                    INSERT INTO rmp_cache(professor_name, rmp_data, expires_at)
                    SELECT 'Test Professor ' || n,
                           jsonb_build_object('rmp_score', 4, 'rmp_difficulty', 3,
                                              'rmp_num_ratings', 20, 'rmp_tags', jsonb_build_array()),
                           now() + interval '1 day'
                    FROM generate_series(1, 5) n
                """))
            # Match the real API's pool size, but connect only to this run's schema.
            engine = create_async_engine(config.url, pool_size=3, max_overflow=2, pool_pre_ping=True,
                connect_args={"server_settings": {"search_path": database.schema_name}})
            application.state.engine = engine
            application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                yield
            finally:
                await engine.dispose()

    app.router.lifespan_context = lifespan
    # All virtual users originate on loopback. Only this isolated app bypasses
    # IP quotas; production's limiter and deployment entry point are unchanged.
    limiter.enabled = False

    @app.get("/__load_test")
    async def identity():
        return {"token": token, "synthetic_sections": 6000, "rate_limits": False}

    return app
