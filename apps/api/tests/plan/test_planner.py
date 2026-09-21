"""Planner generation and endpoint regressions with synthetic catalog data."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.plan import ParsedDegreeValidated, PlanPreferences, StillNeededItem


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_rule_diagnostics(monkeypatch):
    # These packing regressions own fixed availability/metadata query sequences.
    # The independent post-check and its real generation integration are exercised
    # in test_prerequisite_checks.py, including the additional batched rule read.
    from src.services import plan
    monkeypatch.setattr(plan, "check_plan_prerequisites", AsyncMock(return_value=[]))
    monkeypatch.setattr(plan, "load_prerequisite_rows", AsyncMock(return_value={}))

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


# ── Plan generation correctness ───────────────────────────────────────────────

def test_in_progress_courses_not_in_plan():
    """CS332 is in-progress — must not appear in any semester card."""
    from src.services.plan import generate_plan

    session = _make_mock_session()
    plan = asyncio.run(generate_plan(
        make_validated(),
        PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}),
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
        PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}),
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
        PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}),
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
        PlanPreferences.model_validate({"courses": ["PHYS310"], "credits_per_semester": 15}),
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
        PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}),
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
        PlanPreferences.model_validate({"courses": [], "credits_per_semester": 12}),
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
        validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 3}), session,
    ))

    assert len(plan.semesters) == 2, "Two 4-credit courses at target=3 must never combine into one semester"
    assert plan.semesters[0].total_credits == 4
    assert [c.course_code for c in plan.semesters[0].courses] == ["CS491"]
    assert plan.semesters[1].total_credits == 4
    assert [c.course_code for c in plan.semesters[1].courses] == ["CS435"]


# ── Requirement labels and dependency placement ──────────────────────────

class TestRequirementLabelsAndDependencies:
    """Recorded ordering and credit behavior remain independent of labels."""

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


    @pytest.mark.parametrize("credit_target", [3])
    def test_thin_final_capstone_semester_preserves_only_required_credits(self, credit_target):
        """A credit target is a packing ceiling, not a minimum course load."""
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Senior Seminar", options=["HSS404"]),
        ])
        session = self._mock_session(1, [
            {"course_code": "HSS404", "credits": 3, "title": "Seminar", "prerequisites": []},
        ])
        plan = asyncio.run(generate_plan(
            validated, PlanPreferences(credits_per_semester=credit_target), session,
        ))

        assert len(plan.semesters) == 1
        last = plan.semesters[0]
        assert last.total_credits == 3
        assert [course.course_code for course in last.courses] == ["HSS404"]
        assert plan.projected_graduation == last.term_label


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


    def test_missing_prerequisite_does_not_block_and_warns(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="Capstone", options=["CS491"]),
        ])
        session = self._mock_session(1, [
            {"course_code": "CS491", "credits": 3, "title": "Capstone", "prerequisites": ["CS490"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}), session,
        ))

        assert len(plan.semesters) == 1
        assert plan.semesters[0].courses[0].course_code == "CS491"
        prereq_warnings = [w for w in plan.warnings if "CS491" in w and "could not be verified" in w]
        assert len(prereq_warnings) == 1


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
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 3}), session,
        ))

        assert len(plan.semesters) == 5
        assert [c.course_code for c in plan.semesters[3].courses] == ["CS100"]
        assert [c.course_code for c in plan.semesters[4].courses] == ["CS288"]

    def test_two_node_cycle_terminates_and_both_courses_get_scheduled(self):
        from src.services.plan import generate_plan

        validated = make_validated(still_needed=[
            StillNeededItem(requirement="A", options=["AAA100"]),
            StillNeededItem(requirement="B", options=["BBB100"]),
        ])
        session = self._mock_session(2, [
            {"course_code": "AAA100", "credits": 3, "title": "A", "prerequisites": ["BBB100"]},
            {"course_code": "BBB100", "credits": 3, "title": "B", "prerequisites": ["AAA100"]},
        ])

        plan = asyncio.run(generate_plan(
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}), session,
        ))

        placed_codes = {c.course_code for sem in plan.semesters for c in sem.courses}
        assert placed_codes == {"AAA100", "BBB100"}
        cycle_warnings = [w for w in plan.warnings if "AAA100" in w and "BBB100" in w]
        assert len(cycle_warnings) == 1


# ── Credit target validation ─────────────────────────────────────────────────
#
# PlanPreferences is validated before the planner consumes it. Its public request
# boundary must reject invalid credit targets independently of frontend controls.


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
        session.execute = AsyncMock(side_effect=[availability_empty, course_result, availability_empty])
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
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}), session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.course_code == "CHEM121"
        assert course.badge == "Elective"
        assert "3" in course.reason
        assert "Natural Sciences Elective" in course.reason


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
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}), session,
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
            validated, PlanPreferences.model_validate({"courses": [], "credits_per_semester": 15}), session,
        ))

        course = plan.semesters[0].courses[0]
        assert course.title == "Advanced Data Structures and Algorithm Design"
