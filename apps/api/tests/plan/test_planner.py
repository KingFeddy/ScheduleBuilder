"""
Step 0 — Planner engine tests written before implementation.

Tests for functions that don't exist yet will fail with ImportError until
the corresponding implementation step is complete. This is the expected
TDD workflow: the failures drive the implementation.

Implementation steps:
  Step 1 — time_utils: get_planning_terms, term_to_label, get_current_njit_term, get_next_njit_term
  Step 2 — plan.py: matches_wildcard, find_matching_requirement
  Step 3 — plan.py: get_course_data
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


def test_single_oversized_course_forces_its_own_semester():
    """
    At the new UI minimum (3 credits/semester), a normal-sized NJIT course
    (4 credits) can't fit under budget at all. The force-add fallback must
    still place it — exceeding the target for that one semester rather than
    getting stuck — and the next semester must start fresh, not inherit the
    overflow. Two 4-credit courses must never be combined into one semester
    at this target (4 + 4 = 8 > 3).
    """
    from src.services.plan import generate_plan

    validated = make_validated(still_needed=[
        StillNeededItem(requirement="Major Requirement", options=["CS491"]),
        StillNeededItem(requirement="Systems", options=["CS435"]),
    ])

    def _make_result(rows):
        result = MagicMock()
        result.mappings.return_value = rows or []
        return result

    # Each unresolved still_needed item triggers its own availability
    # query (select_best_option, Priority 2) before the single batched
    # course-credits query fires — two items means two empty availability
    # results, then the real course-credits result.
    availability_empty = _make_result(None)
    course_result = _make_result([
        {"course_code": "CS491", "credits": 4, "title": "Computer Science Project", "prerequisites": []},
        {"course_code": "CS435", "credits": 4, "title": "Advanced Data Structures", "prerequisites": []},
    ])
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[availability_empty, availability_empty, course_result])

    plan = asyncio.run(generate_plan(
        validated, {"courses": [], "credits_per_semester": 3}, session,
    ))

    assert len(plan.semesters) == 2, "Two 4-credit courses at target=3 must never combine into one semester"
    assert plan.semesters[0].total_credits == 4
    assert [c.course_code for c in plan.semesters[0].courses] == ["CS491"]
    assert plan.semesters[1].total_credits == 4
    assert [c.course_code for c in plan.semesters[1].courses] == ["CS435"]


def test_mixed_course_sizes_defer_oversized_course_to_later_semester():
    """
    At the new UI minimum (3 credits/semester), courses that fit under
    budget (3-credit) are packed first each pass; a 4-credit course that
    can never fit alongside anything at this target is deferred, not
    force-added early, until it's the only thing left in the pool.
    Verified by running the real planner before writing these assertions.
    """
    from src.services.plan import generate_plan

    validated = make_validated(still_needed=[
        StillNeededItem(requirement="Senior Project", options=["CS491"]),
        StillNeededItem(requirement="Systems", options=["CS435"]),
        StillNeededItem(requirement="GER Humanities", options=["HIST213"]),
    ])

    def _make_result(rows):
        result = MagicMock()
        result.mappings.return_value = rows or []
        return result

    availability_empty = _make_result(None)
    course_result = _make_result([
        {"course_code": "CS491", "credits": 4, "title": "Computer Science Project", "prerequisites": []},
        {"course_code": "CS435", "credits": 3, "title": "Advanced Data Structures", "prerequisites": []},
        {"course_code": "HIST213", "credits": 3, "title": "GER Humanities Course", "prerequisites": []},
    ])
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[availability_empty, availability_empty, availability_empty, course_result])

    plan = asyncio.run(generate_plan(
        validated, {"courses": [], "credits_per_semester": 3}, session,
    ))

    assert len(plan.semesters) == 3
    assert [c.course_code for c in plan.semesters[0].courses] == ["CS435"]
    assert plan.semesters[0].total_credits == 3
    assert [c.course_code for c in plan.semesters[1].courses] == ["HIST213"]
    assert plan.semesters[1].total_credits == 3
    assert [c.course_code for c in plan.semesters[2].courses] == ["CS491"]
    assert plan.semesters[2].total_credits == 4


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


# ── Last-semester requirement detection ────────────────────────────────────

class TestIsLastSemesterRequirement:

    def test_senior_project_matches(self):
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("Senior Project") is True

    def test_senior_seminar_matches(self):
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("Senior Seminar") is True

    def test_capstone_design_matches(self):
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("Capstone Design") is True

    def test_case_insensitive(self):
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("SENIOR SEMINAR") is True
        assert _is_last_semester_requirement("capstone project") is True

    def test_unrelated_requirement_does_not_match(self):
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("GER Humanities") is False
        assert _is_last_semester_requirement("Systems") is False
        assert _is_last_semester_requirement("Tech Elective") is False

    def test_substring_match_not_exact_match(self):
        """A requirement label doesn't need to equal a keyword exactly —
        containing one anywhere is enough, matching real DegreeWorks
        phrasing variety."""
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("Senior Design Project I") is True

    def test_bare_seminar_without_senior_or_capstone_does_not_match(self):
        """'seminar' alone was removed from the keyword list — it caught
        nothing 'senior'/'capstone' didn't already catch, and it falsely
        matched non-terminal requirements like First Year Seminar."""
        from src.services.plan import _is_last_semester_requirement
        assert _is_last_semester_requirement("First Year Seminar") is False
        assert _is_last_semester_requirement("Freshman Seminar") is False


class TestCapstoneLastSemester:
    """
    Integration-level tests through the real generate_plan(). Every
    expected value was verified by actually running this code during
    design, not hand-derived.
    """

    def _mock_session(self, still_needed_count, course_rows):
        def _make_result(rows):
            result = MagicMock()
            result.mappings.return_value = rows or []
            return result

        availability_empty = _make_result(None)
        course_result = _make_result(course_rows)
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[availability_empty] * still_needed_count + [course_result]
        )
        return session

    def test_capstone_merges_into_last_normal_semester_when_room_exists(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro", options=["CS100"]),
            StillNeededItem(requirement="Senior Seminar", options=["HSS404"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS100", "credits": 3, "title": "Intro", "prerequisites": []},
            {"course_code": "HSS404", "credits": 3, "title": "Seminar", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 1
        assert {c.course_code for c in plan.semesters[0].courses} == {"CS100", "HSS404"}
        assert plan.semesters[0].total_credits == 6

    def test_capstone_spills_to_new_semester_when_no_room(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro", options=["CS100"]),
            StillNeededItem(requirement="Senior Seminar", options=["HSS404"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS100", "credits": 3, "title": "Intro", "prerequisites": []},
            {"course_code": "HSS404", "credits": 3, "title": "Seminar", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 3}, session,
        ))

        assert len(plan.semesters) == 2
        assert [c.course_code for c in plan.semesters[0].courses] == ["CS100"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["HSS404"]

    def test_capstone_with_its_own_prerequisite_still_deferred_correctly(self):
        """A flagged course that also has a real prerequisite must still
        wait for that prerequisite's actual placement — being flagged
        doesn't let it skip ADR-27's dependency check, it only adds an
        additional constraint on top."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Project", options=["CS490"]),
            StillNeededItem(requirement="Senior Project", options=["CS491"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS490", "credits": 3, "title": "Project", "prerequisites": []},
            {"course_code": "CS491", "credits": 3, "title": "Capstone", "prerequisites": ["CS490"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 2
        assert [c.course_code for c in plan.semesters[0].courses] == ["CS490"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["CS491"]

    def test_multiple_capstone_courses_overflow_into_extra_semester_not_over_budget(self):
        from src.services.plan import generate_plan

        still_needed = [
            StillNeededItem(requirement=f"Senior Seminar {i}", options=[f"HSS40{i}"])
            for i in range(1, 6)
        ]
        validated = make_validated(still_needed=still_needed)
        session = self._mock_session(5, [
            {"course_code": f"HSS40{i}", "credits": 3, "title": f"Seminar {i}", "prerequisites": []}
            for i in range(1, 6)
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 12}, session,
        ))

        assert len(plan.semesters) == 2
        assert plan.semesters[0].total_credits == 12
        assert len(plan.semesters[0].courses) == 4
        assert plan.semesters[1].total_credits == 3
        assert len(plan.semesters[1].courses) == 1
        all_placed = {c.course_code for sem in plan.semesters for c in sem.courses}
        assert all_placed == {f"HSS40{i}" for i in range(1, 6)}

    def test_no_capstone_courses_is_byte_identical_to_adr27_behavior(self):
        """Regression: zero flagged courses must produce the exact same
        output as before this feature existed — confirms this is purely
        additive."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro", options=["CS100"]),
            StillNeededItem(requirement="Systems", options=["CS435"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS100", "credits": 3, "title": "Intro", "prerequisites": []},
            {"course_code": "CS435", "credits": 3, "title": "Systems", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 1
        assert {c.course_code for c in plan.semesters[0].courses} == {"CS100", "CS435"}
        assert plan.semesters[0].total_credits == 6

    def test_normal_item_depending_on_capstone_item_drops_edge_and_warns_instead_of_hanging(self):
        """
        A normal item's prerequisite resolving to a capstone-flagged item
        must not create an unsatisfiable cross-phase dependency. Phase 1
        packing never places capstone items, so placed_at would never gain
        an entry for that index and the normal item would stay permanently
        blocked — an infinite loop in _pack_semesters' `while items_pool:`
        with no `await` inside it, hanging the whole event loop, not just
        one request. The edge must be dropped and surfaced as a warning
        instead of silently ignored or left to hang.
        """
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Tech Elective", options=["CS500"]),
            StillNeededItem(requirement="Senior Project", options=["CS491"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS500", "credits": 3, "title": "Elective", "prerequisites": ["CS491"]},
            {"course_code": "CS491", "credits": 3, "title": "Capstone", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert {c.course_code for sem in plan.semesters for c in sem.courses} == {"CS500", "CS491"}
        placed_terms = {
            c.course_code: sem_idx
            for sem_idx, sem in enumerate(plan.semesters)
            for c in sem.courses
        }
        assert placed_terms["CS500"] <= placed_terms["CS491"], (
            "CS500 must not be deferred to wait for CS491 — the cross-phase "
            "edge must be dropped, not honored"
        )
        assert any("CS500" in w for w in plan.warnings), (
            "Dropping the cross-phase edge must be surfaced in a warning naming CS500"
        )

    def test_capstone_listed_first_and_bigger_still_lands_after_normal_course(self):
        """
        Old single-phase code sorts by credits-descending, so a bigger capstone
        course listed first in still_needed would get packed into semester 0
        ahead of a smaller normal course — reproducing the exact bug this
        feature exists to fix. This is a discriminating regression test: it
        only passes if the two-phase split is genuinely running, not just if
        must_be_last is tagged but unused.
        """
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Senior Project", options=["CS491"]),
            StillNeededItem(requirement="Systems", options=["CS435"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS491", "credits": 4, "title": "Capstone", "prerequisites": []},
            {"course_code": "CS435", "credits": 3, "title": "Systems", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 4}, session,
        ))

        assert len(plan.semesters) == 2
        assert [c.course_code for c in plan.semesters[0].courses] == ["CS435"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["CS491"]

    def test_capstone_chain_serializes_across_consecutive_trailing_semesters(self):
        """
        A capstone item depending on another capstone item must still be
        serialized across separate semesters, even with generous credit
        headroom — this is the one code path unique to Phase 2 (checking
        dependencies among items in its own pool, not inherited via
        placed_at from Phase 1). Verified by the final review by actually
        running this exact scenario.
        """
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Senior Seminar I",   options=["HSS400"]),
            StillNeededItem(requirement="Senior Seminar II",  options=["HSS401"]),
            StillNeededItem(requirement="Senior Capstone",    options=["HSS402"]),
        ])
        session = self._mock_session(3, [
            {"course_code": "HSS400", "credits": 3, "title": "Seminar I",  "prerequisites": []},
            {"course_code": "HSS401", "credits": 3, "title": "Seminar II", "prerequisites": ["HSS400"]},
            {"course_code": "HSS402", "credits": 3, "title": "Capstone",   "prerequisites": ["HSS401"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 3
        assert [c.course_code for c in plan.semesters[0].courses] == ["HSS400"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["HSS401"]
        assert [c.course_code for c in plan.semesters[2].courses] == ["HSS402"]


# ── Prerequisite dependency graph ───────────────────────────────────────────

class TestComputePrerequisiteDependencies:
    """
    Pure-function tests — no DB, no async. `_ResolvedItem` instances are
    constructed directly; `credits`/`title`/`badge`/`reason` are
    irrelevant to this function and left at their defaults.
    """

    def test_two_course_chain_creates_a_dependency_edge(self):
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [
            _ResolvedItem(requirement="Advanced", course_code="CS435"),
            _ResolvedItem(requirement="Intro",    course_code="CS288"),
        ]
        prereqs = {"CS435": ["CS288"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress=set(), prerequisites_by_code=prereqs,
        )

        assert depends_on[0] == {1}, "CS435 (index 0) must depend on CS288 (index 1)"
        assert depends_on[1] == set()
        assert warnings == []

    def test_completed_prerequisite_creates_no_edge(self):
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [_ResolvedItem(requirement="Advanced", course_code="CS435")]
        prereqs = {"CS435": ["CS288"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed={"CS288"}, in_progress=set(), prerequisites_by_code=prereqs,
        )

        assert depends_on[0] == set()
        assert warnings == []

    def test_in_progress_prerequisite_creates_no_edge(self):
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [_ResolvedItem(requirement="Advanced", course_code="CS435")]
        prereqs = {"CS435": ["CS288"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress={"CS288"}, prerequisites_by_code=prereqs,
        )

        assert depends_on[0] == set()
        assert warnings == []

    def test_prerequisite_not_found_in_plan_creates_no_edge_and_warns(self):
        """CS491 requires CS490, but CS490 isn't anywhere in this plan —
        not completed, not in progress, not itself being scheduled. Must
        not create a dependency edge (nothing to point at), but must be
        named in a warning."""
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [_ResolvedItem(requirement="Capstone", course_code="CS491")]
        prereqs = {"CS491": ["CS490"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress=set(), prerequisites_by_code=prereqs,
        )

        assert depends_on[0] == set()
        assert len(warnings) == 1
        assert "CS491" in warnings[0]

    def test_two_node_cycle_terminates_drops_the_closing_edge_and_warns(self):
        """Real curriculum data should never have a cycle, but the scraped
        data isn't hand-verified — this must not hang or crash."""
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [
            _ResolvedItem(requirement="A", course_code="AAA"),
            _ResolvedItem(requirement="B", course_code="BBB"),
        ]
        prereqs = {"AAA": ["BBB"], "BBB": ["AAA"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress=set(), prerequisites_by_code=prereqs,
        )

        # Exactly one direction of the cycle survives as a real edge; the
        # other is dropped to break the loop. Both courses are flagged.
        assert depends_on[0] != depends_on[1]
        assert (depends_on[0] == {1} and depends_on[1] == set()) or \
               (depends_on[1] == {0} and depends_on[0] == set())
        assert len(warnings) == 1
        assert "AAA" in warnings[0] and "BBB" in warnings[0]

    def test_no_prerequisite_data_creates_no_edges(self):
        """Baseline: with no prerequisite data at all, every item's
        dependency set must be empty — this function must never invent a
        constraint where none exists."""
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [
            _ResolvedItem(requirement="A", course_code="CS101"),
            _ResolvedItem(requirement="B", course_code="CS201"),
            _ResolvedItem(requirement="C", course_code=None),  # TBD
        ]

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress=set(), prerequisites_by_code={},
        )

        assert depends_on == [set(), set(), set()]
        assert warnings == []

    def test_three_deep_chain_creates_transitive_edges(self):
        """CS491 -> CS490 -> CS288, a real chain shape from live curriculum
        data. Each item depends only on its DIRECT prerequisite's index —
        transitivity is the caller's problem when walking the graph, not
        this function's."""
        from src.services.plan import _compute_prerequisite_dependencies, _ResolvedItem

        resolved = [
            _ResolvedItem(requirement="Capstone", course_code="CS491"),
            _ResolvedItem(requirement="Project",  course_code="CS490"),
            _ResolvedItem(requirement="Intro",    course_code="CS288"),
        ]
        prereqs = {"CS491": ["CS490"], "CS490": ["CS288"]}

        depends_on, warnings = _compute_prerequisite_dependencies(
            resolved, completed=set(), in_progress=set(), prerequisites_by_code=prereqs,
        )

        assert depends_on == [{1}, {2}, set()]
        assert warnings == []


# ── Prerequisite-aware planning (integration) ───────────────────────────────

class TestPrerequisiteAwarePlanning:
    """
    Integration-level tests through the real generate_plan(). Every
    expected value was verified by actually running this code during
    design, not hand-derived — see the plan/spec for the exact
    calibration runs.
    """

    def _mock_session(self, still_needed_count, course_rows):
        def _make_result(rows):
            result = MagicMock()
            result.mappings.return_value = rows or []
            return result

        availability_empty = _make_result(None)
        course_result = _make_result(course_rows)
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[availability_empty] * still_needed_count + [course_result]
        )
        return session

    def test_prerequisite_scheduled_in_a_strictly_earlier_semester(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro", options=["CS288"]),
            StillNeededItem(requirement="Adv",   options=["CS435"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "CS288", "credits": 3, "title": "Intro", "prerequisites": []},
            {"course_code": "CS435", "credits": 3, "title": "Adv", "prerequisites": ["CS288"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 2
        assert [c.course_code for c in plan.semesters[0].courses] == ["CS288"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["CS435"]

    def test_completed_prerequisite_imposes_no_delay(self):
        from src.services.plan import generate_plan

        validated = make_validated(
            completed_courses=["CS288", "CS280"],
            in_progress_courses=[],
            still_needed=[StillNeededItem(requirement="Adv", options=["CS435"])],
        )
        session = self._mock_session(1, [
            {"course_code": "CS435", "credits": 3, "title": "Adv", "prerequisites": ["CS288"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 1
        assert plan.semesters[0].courses[0].course_code == "CS435"

    def test_in_progress_prerequisite_imposes_no_delay(self):
        from src.services.plan import generate_plan

        validated = make_validated(
            completed_courses=[],
            in_progress_courses=["CS288", "CS332"],
            still_needed=[StillNeededItem(requirement="Adv", options=["CS435"])],
        )
        session = self._mock_session(1, [
            {"course_code": "CS435", "credits": 3, "title": "Adv", "prerequisites": ["CS288"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 1
        assert plan.semesters[0].courses[0].course_code == "CS435"

    def test_missing_prerequisite_does_not_block_and_warns(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Capstone", options=["CS491"]),
        ])
        session = self._mock_session(1, [
            {"course_code": "CS491", "credits": 3, "title": "Capstone", "prerequisites": ["CS490"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 1
        assert plan.semesters[0].courses[0].course_code == "CS491"
        prereq_warnings = [w for w in plan.warnings if "CS491" in w and "could not be verified" in w]
        assert len(prereq_warnings) == 1

    def test_three_deep_chain_produces_three_semesters(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Capstone", options=["CS491"]),
            StillNeededItem(requirement="Project",  options=["CS490"]),
            StillNeededItem(requirement="Intro",    options=["CS288"]),
        ])
        session = self._mock_session(3, [
            {"course_code": "CS491", "credits": 3, "title": "Capstone", "prerequisites": ["CS490"]},
            {"course_code": "CS490", "credits": 3, "title": "Project",  "prerequisites": ["CS288"]},
            {"course_code": "CS288", "credits": 3, "title": "Intro",    "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert len(plan.semesters) == 3
        assert [c.course_code for c in plan.semesters[0].courses] == ["CS288"]
        assert [c.course_code for c in plan.semesters[1].courses] == ["CS490"]
        assert [c.course_code for c in plan.semesters[2].courses] == ["CS491"]

    def test_credit_contention_delays_prerequisite_and_dependent_still_waits(self):
        """
        The exact scenario that caught the static-earliest-bound bug during
        design: three unrelated 3-credit courses at credit_target=3 delay a
        1-credit prerequisite (CS100) three semesters past its theoretical
        minimum. Its 1-credit dependent (CS288) must still land in a
        strictly LATER semester than wherever CS100 actually ends up — not
        the same one.
        """
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Z1", options=["CS201"]),
            StillNeededItem(requirement="Z2", options=["CS202"]),
            StillNeededItem(requirement="Z3", options=["CS203"]),
            StillNeededItem(requirement="A",  options=["CS100"]),
            StillNeededItem(requirement="B",  options=["CS288"]),
        ])
        session = self._mock_session(5, [
            {"course_code": "CS201", "credits": 3, "title": "Z1", "prerequisites": []},
            {"course_code": "CS202", "credits": 3, "title": "Z2", "prerequisites": []},
            {"course_code": "CS203", "credits": 3, "title": "Z3", "prerequisites": []},
            {"course_code": "CS100", "credits": 1, "title": "A",  "prerequisites": []},
            {"course_code": "CS288", "credits": 1, "title": "B",  "prerequisites": ["CS100"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 3}, session,
        ))

        assert len(plan.semesters) == 5
        assert [c.course_code for c in plan.semesters[3].courses] == ["CS100"]
        assert [c.course_code for c in plan.semesters[4].courses] == ["CS288"]

    def test_two_node_cycle_terminates_and_both_courses_get_scheduled(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="A", options=["AAA"]),
            StillNeededItem(requirement="B", options=["BBB"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "AAA", "credits": 3, "title": "A", "prerequisites": ["BBB"]},
            {"course_code": "BBB", "credits": 3, "title": "B", "prerequisites": ["AAA"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        placed_codes = {c.course_code for sem in plan.semesters for c in sem.courses}
        assert placed_codes == {"AAA", "BBB"}
        cycle_warnings = [w for w in plan.warnings if "AAA" in w and "BBB" in w]
        assert len(cycle_warnings) == 1

    def test_disclaimer_text_updated(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro", options=["CS288"]),
        ])
        session = self._mock_session(1, [
            {"course_code": "CS288", "credits": 3, "title": "Intro", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        assert (
            "orders courses using scraped prerequisite data" in " ".join(plan.warnings)
        )
        assert not any("does not verify course prerequisites" in w for w in plan.warnings)


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


# ── Elective detection and title fallback ──────────────────────────────────────

class TestElectiveDetectionAndTitleFallback:
    """
    Real user report: a requirement with many valid options (e.g. a ~25-option
    Math Elective, a 7-option Natural Science Elective) was silently resolved
    to one course and labeled "Required" — identical to a genuinely
    single-option requirement, giving no signal that alternatives existed.
    Separately, courses never scraped into `courses` (no title data) showed
    their bare course code as the title twice ("IS350 | IS350"), discarding
    the much more informative DegreeWorks requirement name we already have.

    Each test here has exactly one unresolved still_needed item, which
    triggers its own availability query (select_best_option, Priority 2)
    before the batched course-data query — mock ordering must account for
    this (see the comment on _make_mock_session's own limits elsewhere in
    this file), so a local helper is used instead of _make_mock_session.
    """

    def _mock_session(self, course_rows):
        availability_empty = MagicMock()
        availability_empty.mappings.return_value = []
        course_result = MagicMock()
        course_result.mappings.return_value = course_rows

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[availability_empty, course_result])
        return session

    def test_multi_option_requirement_gets_elective_badge_and_names_option_count(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(
                requirement="Natural Sciences Elective",
                options=["CHEM121", "CHEM125", "PHYS202"],
            ),
        ])
        session = self._mock_session([
            {"course_code": "CHEM121", "credits": 3, "title": "Fundamentals of Chemical Principles I", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.course_code == "CHEM121"
        assert course.badge == "Elective"
        assert "3" in course.reason
        assert "Natural Sciences Elective" in course.reason

    def test_single_option_requirement_keeps_required_badge(self):
        """Regression: a requirement with exactly one real option must not be
        relabeled — it genuinely is a fixed requirement, not a choice."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Intro to Data Reduction", options=["PHYS114"]),
        ])
        session = self._mock_session([
            {"course_code": "PHYS114", "credits": 3, "title": "Intro to Data Reduction with Applications", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.course_code == "PHYS114"
        assert course.badge == "Required"

    def test_missing_title_falls_back_to_requirement_name(self):
        """A course never scraped into `courses` has no title data — falling
        back to the bare course code twice ("IS350 | IS350") is far less
        informative than the DegreeWorks requirement name we already have
        ("Computers, Society, and Ethics")."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Computers, Society, and Ethics", options=["IS350"]),
        ])
        session = self._mock_session([])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.course_code == "IS350"
        assert course.title == "Computers, Society, and Ethics"

    def test_present_title_is_not_overridden_by_requirement_name(self):
        """Regression: the fallback must only kick in when title is genuinely
        missing — a real scraped title must never be discarded."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Some Generic Requirement Label", options=["CS435"]),
        ])
        session = self._mock_session([
            {"course_code": "CS435", "credits": 3, "title": "Advanced Data Structures and Algorithm Design", "prerequisites": []},
        ])

        plan = asyncio.run(generate_plan(
            validated, {"courses": [], "credits_per_semester": 15}, session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.title == "Advanced Data Structures and Algorithm Design"
