import pytest
from sqlalchemy import text

from src.config import settings


@pytest.mark.asyncio
async def test_terms_come_from_real_section_rows_without_duplicates(db_session, monkeypatch):
    from src.services.terms import discover_terms
    monkeypatch.setattr(settings, "CURRENT_TERM", "202790")
    async with db_session.begin():
        await db_session.execute(text("""
            INSERT INTO sections (crn, term, course_code) VALUES
              ('99901', '202690', 'CS999'), ('99902', '202690', 'CS999'),
              ('99901', '202710', 'CS999'), ('99901', '202750', 'CS999')
        """))
        await db_session.execute(text("""
            INSERT INTO sections (crn, term, course_code) VALUES ('99901', :term, 'CS999')
        """), [{"term": term} for term in ["BADTERM", "202600", "202690\n", "２０２６90"]])
    response = await discover_terms(db_session)
    assert response.default_term == "202790"
    assert [(t.code, t.has_data) for t in response.terms] == [
        ("202690", True), ("202710", True), ("202750", True), ("202790", False),
    ]


@pytest.mark.asyncio
async def test_empty_database_does_not_invent_available_terms(db_session, monkeypatch):
    from src.services.terms import discover_terms
    monkeypatch.setattr(settings, "CURRENT_TERM", "202810")
    response = await discover_terms(db_session)
    assert response.model_dump() == {
        "default_term": "202810",
        "terms": [{"code": "202810", "label": "Spring 2028", "has_data": False}],
    }
