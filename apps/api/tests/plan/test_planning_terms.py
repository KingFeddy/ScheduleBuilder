import pytest

from src.config import settings
from src.scheduler.time_utils import get_planning_terms
from tests.plan.test_catalog_coverage import plan_for


@pytest.mark.parametrize("start,expected", [
    ("202690", ["202690", "202710", "202790"]),
    ("202710", ["202710", "202790", "202810"]),
    ("202750", ["202790", "202810", "202890"]),
])
def test_planning_term_sequence_starts_from_explicit_default(start, expected):
    assert get_planning_terms(n=3, start_term=start) == expected


def test_planning_can_include_summer_without_a_second_default():
    assert get_planning_terms(n=3, start_term="202710", skip_summer=False) == ["202710", "202750", "202790"]


@pytest.mark.parametrize("default,first", [("202710", "202710"), ("202750", "202790")])
def test_generated_plan_uses_the_configured_default(monkeypatch, default, first):
    monkeypatch.setattr(settings, "CURRENT_TERM", default)
    plan = plan_for("CS999", [])
    assert plan.semesters[0].term == first


def test_explicit_start_overrides_server_default_without_collected_sections(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from src.schemas.plan import PlanPreferences
    from src.services.plan import generate_plan
    from tests.plan.test_planner import make_validated

    start = "203190"
    monkeypatch.setattr(settings, "CURRENT_TERM", "202510")
    result = MagicMock()
    result.mappings.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    generated = asyncio.run(generate_plan(make_validated(), PlanPreferences(start_term=start), session))
    assert generated.semesters[0].term == start
    assert all(s.term >= start for s in generated.semesters)


@pytest.mark.parametrize("start", ["", "2026", "202650", "202699", "000010", "999990", 202690, True])
def test_invalid_or_unsupported_start_is_rejected(start):
    from pydantic import ValidationError
    from src.schemas.plan import PlanPreferences
    with pytest.raises(ValidationError):
        PlanPreferences(start_term=start)
