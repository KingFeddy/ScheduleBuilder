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


@pytest.mark.parametrize("default,first", [("202710", "202710"), ("202750", "202790"), ("202790", "202790")])
def test_generated_plan_uses_the_configured_default(monkeypatch, default, first):
    monkeypatch.setattr(settings, "CURRENT_TERM", default)
    plan = plan_for("CS999", [])
    assert plan.semesters[0].term == first
