"""
Step 0 — Planner engine tests written before implementation.

Tests for functions that don't exist yet will fail with ImportError until
the corresponding implementation step is complete. This is the expected
TDD workflow: the failures drive the implementation.

Implementation steps:
  Step 1 — time_utils: get_planning_terms, term_to_label, get_current_njit_term, get_next_njit_term
  Step 2 — plan.py: matches_wildcard, find_matching_requirement
  Step 3 — plan.py: get_course_credits_and_titles
  Step 4 — plan.py: generate_plan (full planner)
  Step 5 — routers/plan.py: POST /api/plan/generate endpoint

Run after each step: the failure count should drop.
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import ParsedDegreeValidated, ParseValidationError, StillNeededItem


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_session(course_rows=None, section_rows=None):
    """
    Returns a mock AsyncSession whose execute() calls return empty mappings by
    default. Pass course_rows / section_rows to seed specific DB results.

    The planner makes two types of queries:
      1. SELECT course_code, credits, title FROM courses WHERE course_code = ANY(:codes)
      2. SELECT DISTINCT course_code FROM sections WHERE course_code = ANY(:codes) AND term = :term

    Returning empty lists for both causes the planner to:
      - Default all unknown courses to 3 credits
      - Skip availability-based option selection (fall back to options[0])
    """
    def _make_result(rows):
        result = MagicMock()
        result.mappings.return_value = rows or []
        return result

    # Each call to session.execute returns results in order:
    # call 0 → courses table (credits + titles)
    # call 1+ → sections table (availability per select_best_option call)
    course_result   = _make_result(course_rows)
    section_result  = _make_result(section_rows)

    session = AsyncMock()
    # side_effect cycles: first call gets course_result, all subsequent get section_result
    session.execute = AsyncMock(
        side_effect=[course_result] + [section_result] * 20
    )
    return session


def make_validated(**overrides) -> ParsedDegreeValidated:
    defaults = dict(
        majors=["Computer Science"],
        credits_completed=106,
        credits_required=124,
        credits_remaining=18,
        completed_courses=["CS280", "CS331"],
        in_progress_courses=["CS332"],
        still_needed=[
            StillNeededItem(requirement="Senior Project",  options=["CS491"]),
            StillNeededItem(requirement="Systems",         options=["CS435"]),
            StillNeededItem(requirement="Algorithms",      options=["CS435", "CS480"]),
            StillNeededItem(requirement="Tech elective",   options=["CS4XX"]),
            StillNeededItem(requirement="GER Humanities",  options=["HIST3XX"]),
            StillNeededItem(requirement="GER Social",      options=["PSY210"]),
        ],
    )
    defaults.update(overrides)
    return ParsedDegreeValidated(**defaults)


# ── Term computation ──────────────────────────────────────────────────────────

class TestTermUtils:

    def test_term_label_spring(self):
        from src.scheduler.time_utils import term_to_label
        assert term_to_label("202710") == "Spring 2027"

    def test_term_label_fall(self):
        from src.scheduler.time_utils import term_to_label
        assert term_to_label("202690") == "Fall 2026"

    def test_term_label_summer(self):
        from src.scheduler.time_utils import term_to_label
        assert term_to_label("202750") == "Summer 2027"

    def test_planning_terms_skips_summer(self):
        from src.scheduler.time_utils import get_planning_terms
        terms = get_planning_terms(n=6)
        assert all(not t.endswith("50") for t in terms), \
            "Summer terms must be skipped in planning output by default"

    def test_planning_terms_length(self):
        from src.scheduler.time_utils import get_planning_terms
        terms = get_planning_terms(n=4)
        assert len(terms) == 4

    def test_planning_terms_is_sequential(self):
        """Each term must follow the previous in academic calendar order (summer skipped)."""
        from src.scheduler.time_utils import get_planning_terms
        terms = get_planning_terms(n=6)
        for i in range(1, len(terms)):
            year_a, suf_a = int(terms[i - 1][:4]), terms[i - 1][4:]
            year_b, suf_b = int(terms[i][:4]),     terms[i][4:]
            if suf_a == "90":    # Fall → Spring next year
                assert suf_b == "10" and year_b == year_a + 1, \
                    f"After Fall {year_a} expected Spring {year_a + 1}, got {terms[i]}"
            elif suf_a == "10":  # Spring → Fall same year (summer skipped)
                assert suf_b == "90" and year_b == year_a, \
                    f"After Spring {year_a} expected Fall {year_a}, got {terms[i]}"

    def test_get_current_term_returns_valid_format(self):
        from src.scheduler.time_utils import get_current_njit_term
        term = get_current_njit_term()
        assert len(term) == 6
        assert term[4:] in ("10", "50", "90")

    def test_get_next_term_fall_to_spring(self):
        from src.scheduler.time_utils import get_next_njit_term
        assert get_next_njit_term("202690") == "202710"

    def test_get_next_term_spring_to_summer(self):
        from src.scheduler.time_utils import get_next_njit_term
        assert get_next_njit_term("202710") == "202750"

    def test_get_next_term_summer_to_fall(self):
        from src.scheduler.time_utils import get_next_njit_term
        assert get_next_njit_term("202750") == "202790"

    def test_planning_starts_from_current_term_not_hardcoded(self):
        """
        Regression test for the hardcoded CURRENT_TERM='202690' bug.
        In February 2027 the plan must start from Spring 2027, not Fall 2026.
        """
        from src.scheduler import time_utils

        with mock.patch.object(time_utils, "date") as mock_date:
            mock_date.today.return_value = date(2027, 2, 15)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            terms = time_utils.get_planning_terms(n=2)

        assert terms[0] == "202710", (
            f"In February 2027 plan must start from Spring 2027 (202710), got {terms[0]!r}"
        )


# ── Wildcard matching ─────────────────────────────────────────────────────────

class TestWildcardMatching:

    def test_exact_match(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("CS435", "CS435")

    def test_exact_mismatch(self):
        from src.services.plan import matches_wildcard
        assert not matches_wildcard("CS280", "CS435")

    def test_wildcard_3xx(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("PHYS310", "PHYS3XX")

    def test_wildcard_4xx(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("CS480", "CS4XX")

    def test_wildcard_wrong_prefix(self):
        from src.services.plan import matches_wildcard
        assert not matches_wildcard("MATH310", "PHYS3XX")

    def test_wildcard_wrong_level(self):
        from src.services.plan import matches_wildcard
        assert not matches_wildcard("PHYS410", "PHYS3XX")

    def test_universal_wildcard(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("ANYTHING101", "@")

    def test_single_x_matches_one_digit(self):
        """PHYS3X means exactly one wildcard digit — matches PHYS31, not PHYS310."""
        from src.services.plan import matches_wildcard
        assert matches_wildcard("PHYS31", "PHYS3X")
        assert not matches_wildcard("PHYS310", "PHYS3X")

    def test_double_x_matches_two_digits(self):
        """
        THE wildcard contract test (S5 → S6).
        Parser must emit PHYS3XX (two Xs) for DegreeWorks 'PHYS 3@'.
        PHYS3XX → ^PHYS3\\d\\d$ → matches PHYS310, PHYS321, etc.
        """
        from src.services.plan import matches_wildcard
        assert matches_wildcard("PHYS310", "PHYS3XX")
        assert matches_wildcard("PHYS321", "PHYS3XX")
        assert not matches_wildcard("PHYS410", "PHYS3XX")

    def test_case_insensitive(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("phys310", "PHYS3xx")

    def test_no_wildcard_pattern_is_exact_match(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("CS435", "CS435")
        assert not matches_wildcard("CS480", "CS435")

    def test_lowercase_pattern_normalized(self):
        from src.services.plan import matches_wildcard
        assert matches_wildcard("CS480", "cs4xx")


# ── Elective / requirement matching ──────────────────────────────────────────

class TestFindMatchingRequirement:

    def test_exact_match_satisfies_requirement(self):
        from src.services.plan import find_matching_requirement
        items = [StillNeededItem(requirement="Data Structures", options=["CS435"])]
        assert find_matching_requirement("CS435", items, set()) == 0

    def test_wildcard_match_satisfies_requirement(self):
        from src.services.plan import find_matching_requirement
        items = [StillNeededItem(requirement="Physics elective", options=["PHYS3XX"])]
        assert find_matching_requirement("PHYS310", items, set()) == 0, \
            "PHYS310 must match PHYS3XX wildcard"

    def test_exact_preferred_over_wildcard(self):
        """When both an exact and wildcard requirement match, exact comes first."""
        from src.services.plan import find_matching_requirement
        items = [
            StillNeededItem(requirement="Exact req",    options=["CS435"]),
            StillNeededItem(requirement="Wildcard req", options=["CS4XX"]),
        ]
        assert find_matching_requirement("CS435", items, set()) == 0, \
            "Exact match must be preferred over wildcard match"

    def test_already_satisfied_requirement_skipped(self):
        from src.services.plan import find_matching_requirement
        items = [StillNeededItem(requirement="Data Structures", options=["CS435"])]
        assert find_matching_requirement("CS435", items, already_satisfied={0}) is None, \
            "Already-satisfied requirement must be skipped"

    def test_no_match_returns_none(self):
        from src.services.plan import find_matching_requirement
        items = [StillNeededItem(requirement="Req", options=["CS280"])]
        assert find_matching_requirement("HIST301", items, set()) is None

    def test_second_item_matched_when_first_satisfied(self):
        from src.services.plan import find_matching_requirement
        items = [
            StillNeededItem(requirement="First",  options=["CS435"]),
            StillNeededItem(requirement="Second", options=["CS435"]),
        ]
        # First is already satisfied — second should match
        assert find_matching_requirement("CS435", items, already_satisfied={0}) == 1


# ── Plan generation correctness ───────────────────────────────────────────────

def test_in_progress_courses_not_in_plan():
    """CS332 is in-progress — must not appear in any semester card."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    all_codes = [c.course_code for s in plan.semesters for c in s.courses]
    assert "CS332" not in all_codes, "In-progress courses must not appear in plan"


def test_completed_courses_not_in_plan():
    """CS280 and CS331 are completed — must not appear in any semester card."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    all_codes = [c.course_code for s in plan.semesters for c in s.courses]
    assert "CS280" not in all_codes
    assert "CS331" not in all_codes


def test_graduating_student_produces_no_semesters():
    """credits_remaining=0, still_needed=[] → empty plan with congratulations."""
    from src.services.plan import generate_plan

    validated = make_validated(
        credits_completed=124,
        credits_required=124,
        credits_remaining=0,
        still_needed=[],
        in_progress_courses=[],
    )
    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        validated,
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    assert plan.semesters == []
    assert any("congratulations" in w.lower() for w in plan.warnings)


def test_elective_matches_wildcard_requirement():
    """
    Student adds PHYS310 as an elective.
    Still needed has PHYS3XX wildcard requirement.
    PHYS310 must satisfy the requirement — not appear as an extra course.
    """
    from src.services.plan import generate_plan

    validated = make_validated(
        still_needed=[
            StillNeededItem(requirement="Physics elective", options=["PHYS3XX"]),
        ],
        credits_remaining=3,
        credits_completed=121,
        credits_required=124,
        in_progress_courses=[],
    )
    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        validated,
        {"courses": ["PHYS310"], "credits_per_semester": 15},
        session,
    ))
    all_codes  = [c.course_code for s in plan.semesters for c in s.courses]
    all_badges = [c.badge for s in plan.semesters for c in s.courses]

    assert "PHYS310" in all_codes, "PHYS310 must appear in the plan"
    assert all_codes.count("PHYS310") == 1, "PHYS310 must appear exactly once, not duplicated as extra"
    phys_idx = all_codes.index("PHYS310")
    assert all_badges[phys_idx] == "Elective"


def test_all_wildcard_requirement_becomes_tbd():
    """
    A requirement with only wildcard options and no matching student elective
    must emit a TBD placeholder — never silently dropped.
    """
    from src.services.plan import generate_plan

    validated = make_validated(
        still_needed=[
            StillNeededItem(requirement="Tech elective", options=["CS4XX"]),
        ],
        credits_remaining=3,
        credits_completed=121,
        credits_required=124,
        in_progress_courses=[],
    )
    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        validated,
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    tbd_courses = [
        c for s in plan.semesters for c in s.courses if c.badge == "TBD"
    ]
    assert tbd_courses, "All-wildcard requirement with no elective must produce a TBD course"


def test_credit_target_respected_within_tolerance():
    """Non-final semesters must not exceed the credit target by more than one course."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": [], "credits_per_semester": 12},
        session,
    ))
    for sem in plan.semesters[:-1]:  # Last semester is allowed to overflow
        assert sem.total_credits <= 12 + 3, (
            f"Semester {sem.term_label} has {sem.total_credits} credits (target: 12)"
        )


def test_total_credits_matches_course_sum():
    """SemesterCard.total_credits must equal sum of its courses' credit values."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    for sem in plan.semesters:
        expected = sum(c.credits for c in sem.courses)
        assert sem.total_credits == expected, (
            f"{sem.term_label}: total_credits={sem.total_credits} but "
            f"sum(courses.credits)={expected}"
        )


# ── Credit target validation ─────────────────────────────────────────────────
#
# preferences is a raw, untyped dict (routers/plan.py's GenerateRequest) coming
# from a public, unauthenticated API — credits_per_semester must be validated
# at the point it's consumed, not trusted as always 3-24 just because every
# legitimate frontend control happens to send values in that range.

class TestCreditTargetValidation:

    def test_missing_key_defaults_to_15(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        plan = asyncio.run(generate_plan(make_validated(), {"courses": []}, session))
        assert plan.semesters  # didn't raise, produced a plan

    def test_minimum_boundary_3_is_accepted(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        plan = asyncio.run(generate_plan(
            make_validated(), {"courses": [], "credits_per_semester": 3}, session,
        ))
        assert plan.semesters

    def test_maximum_boundary_24_is_accepted(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        plan = asyncio.run(generate_plan(
            make_validated(), {"courses": [], "credits_per_semester": 24}, session,
        ))
        assert plan.semesters

    def test_zero_is_rejected(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        with pytest.raises(ParseValidationError):
            asyncio.run(generate_plan(
                make_validated(), {"courses": [], "credits_per_semester": 0}, session,
            ))

    def test_negative_is_rejected(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        with pytest.raises(ParseValidationError):
            asyncio.run(generate_plan(
                make_validated(), {"courses": [], "credits_per_semester": -5}, session,
            ))

    def test_below_minimum_is_rejected(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        with pytest.raises(ParseValidationError):
            asyncio.run(generate_plan(
                make_validated(), {"courses": [], "credits_per_semester": 2}, session,
            ))

    def test_above_maximum_is_rejected(self):
        from src.services.plan import generate_plan

        session = _make_mock_session()
        with pytest.raises(ParseValidationError):
            asyncio.run(generate_plan(
                make_validated(), {"courses": [], "credits_per_semester": 25}, session,
            ))

    def test_non_numeric_value_is_rejected_cleanly(self):
        """A malformed request (e.g. a string) must raise the same clean,
        catchable error the router already handles — not an unhandled
        TypeError from deep inside the packing loop's arithmetic."""
        from src.services.plan import generate_plan

        session = _make_mock_session()
        with pytest.raises(ParseValidationError):
            asyncio.run(generate_plan(
                make_validated(), {"courses": [], "credits_per_semester": "abc"}, session,
            ))


def test_already_completed_elective_excluded_with_warning():
    """Student adds CS280 as elective, but CS280 is already completed."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": ["CS280"], "credits_per_semester": 15},
        session,
    ))
    elective_cs280 = [
        c for s in plan.semesters for c in s.courses
        if c.course_code == "CS280" and c.badge == "Elective"
    ]
    assert not elective_cs280, "Completed course must not be re-added as an elective"
    assert any("CS280" in w for w in plan.warnings), \
        "Warning must mention the removed elective by course code"


def test_prerequisite_disclaimer_always_present():
    """Plan warnings must always include the prerequisite disclaimer."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    assert any("prerequisite" in w.lower() for w in plan.warnings), \
        "Prerequisite disclaimer must always be present in plan warnings"


def test_plan_is_deterministic():
    """Same input always produces the same output — no randomness or ordering drift."""
    from src.services.plan import generate_plan

    validated = make_validated()
    prefs = {"courses": ["PHYS310"], "credits_per_semester": 15}

    # Two independent calls with fresh sessions must produce identical output
    plan1 = asyncio.run(generate_plan(validated, prefs, _make_mock_session()))
    plan2 = asyncio.run(generate_plan(validated, prefs, _make_mock_session()))

    codes1 = [c.course_code for s in plan1.semesters for c in s.courses]
    codes2 = [c.course_code for s in plan2.semesters for c in s.courses]
    assert codes1 == codes2, "Planner must be deterministic"


def test_no_requirements_dropped():
    """
    Every still_needed item (concrete or TBD) must appear somewhere in the plan.
    Silently dropping a requirement is the most dangerous planner failure.
    """
    from src.services.plan import generate_plan

    session = _make_mock_session()
    validated = make_validated()
    n_requirements = len(validated.still_needed)

    plan = asyncio.run(generate_plan(
        validated,
        {"courses": [], "credits_per_semester": 15},
        session,
    ))
    total_planned = sum(len(s.courses) for s in plan.semesters)
    assert total_planned >= n_requirements, (
        f"Plan has {total_planned} courses but {n_requirements} requirements — "
        "some requirements were silently dropped"
    )
