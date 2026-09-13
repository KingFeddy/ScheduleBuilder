"""Real catalog counts and scraper configuration on the isolated test database."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from src.config import settings


@pytest.mark.asyncio
async def test_catalog_counts_distinguish_configuration_data_and_term(db_session, monkeypatch):
    from src.services.catalog import load_catalog_coverage
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS,HSS,PHYS")
    monkeypatch.setattr(settings, "GER_SUBJECTS", "HSS,HUM")
    async with db_session.begin():
        await db_session.execute(text("""
            INSERT INTO courses (course_code, title, credits) VALUES
                ('ZZZ999', 'Outside configuration', 3), ('PHYS999', NULL, NULL)
        """))
        await db_session.execute(text("""
            INSERT INTO sections (crn, term, course_code) VALUES
                ('99991', '202690', 'CS999'), ('99992', '202690', 'CS999'),
                ('99993', '202610', 'PHYS999')
        """))
    result = await load_catalog_coverage(db_session, "202690")
    subjects = {s.subject: s for s in result.subjects}
    assert result.term == "202690"
    assert result.configured_subjects == ["CS", "HSS", "PHYS"]
    assert subjects["CS"].course_count == 1 and subjects["CS"].section_count == 2
    assert subjects["PHYS"].course_count == 1 and subjects["PHYS"].section_count == 0
    assert subjects["HSS"].configured and subjects["HSS"].course_count == 0
    assert not subjects["ZZZ"].configured and subjects["ZZZ"].course_count == 1
    assert not subjects["HUM"].configured and subjects["HUM"].course_count == 0
    assert any("HUM" in w and "outside" in w for w in result.warnings)
    assert any("HSS" in w and "No catalog" in w for w in result.warnings)
    assert any("PHYS" in w and "No sections" in w for w in result.warnings)
    other_term = await load_catalog_coverage(db_session, "202610")
    assert next(s for s in other_term.subjects if s.subject == "PHYS").section_count == 1


@pytest.mark.asyncio
async def test_cron_uses_configured_subjects(monkeypatch):
    from src.scrapers import cron
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "IS,HSS")
    monkeypatch.setattr(settings, "CURRENT_TERM", "202710")
    engine = MagicMock(dispose=AsyncMock())
    monkeypatch.setattr(cron, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(cron, "async_sessionmaker", MagicMock(return_value=MagicMock(return_value=AsyncMock())))
    banner, rmp = AsyncMock(), AsyncMock()
    monkeypatch.setattr(cron, "run_banner_scrape", banner)
    monkeypatch.setattr(cron, "run_rmp_scrape", rmp)
    await cron.main()
    assert banner.await_args.kwargs["subjects"] == ["IS", "HSS"]
    assert banner.await_args.kwargs["term"] == "202710"
    engine.dispose.assert_awaited_once()
