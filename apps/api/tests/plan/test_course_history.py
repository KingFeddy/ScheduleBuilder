"""Synthetic attempt rows: no personal audit or live catalog required."""
import pytest

from src.schemas.plan import CourseAttempt, ParsedDegree, StillNeededItem
from src.services.dw_parser import _extract_course_attempts
from src.services.plan import validate_parsed_degree


@pytest.mark.parametrize("grade,status,earns_credit", [
    ("A", "passed", True), ("B+", "passed", True), ("C", "passed", True),
    ("D", "passed", True), ("P", "passed", True), ("S", "passed", True),
    ("T", "transfer", True), ("TR", "transfer", True),
    ("F", "failed", False), ("U", "failed", False),
    ("W", "withdrawn", False), ("I", "incomplete", False),
    ("IP", "in_progress", False), ("AU", "audit", False),
    ("WF", "unknown", None), ("INC", "unknown", None), ("XYZ", "unknown", None),
])
def test_full_grade_tokens_control_credit(grade, status, earns_credit):
    attempt, = _extract_course_attempts(f"CS 101 Synthetic Course {grade} 3 2025 Fall")
    assert (attempt.grade, attempt.status, attempt.earns_credit) == (grade, status, earns_credit)
    assert attempt.term == "2025 Fall"
    assert attempt.course_code == "CS101"


def test_repeated_attempts_keep_terms_and_source_without_erasing_a_pass():
    text = "\n".join([
        "CS 101 Synthetic Course F 3 2024 Fall",
        "CS 101 Synthetic Course B+ 3 2025 Spring",
        "CS 101 Synthetic Course IP (3) 2026 Fall",
        "CS 102 Synthetic Course W 3 2025 Fall",
        "CS 103 Synthetic Course I 3 2025 Fall",
    ])
    history = _extract_course_attempts(text, document_id="a" * 64)
    degree = validate_parsed_degree(ParsedDegree(
        majors=["Computer Science"], course_attempts=history,
        completed_courses=["CS102", "CS103", "CS999"],
        in_progress_courses=["CS999"],
        still_needed=[StillNeededItem(requirement="Project", options=["CS491"])],
    ))
    assert degree.completed_courses == ["CS101"]
    assert degree.in_progress_courses == ["CS101"]
    assert len(degree.course_attempts) == 5
    assert history[1].source.line == 2
    assert history[1].source.document_id == "a" * 64
    assert history[1].source.text == text.splitlines()[1]
    assert ParsedDegree.model_validate_json(degree.model_dump_json()).course_attempts == history


def test_missing_terms_fractional_credits_and_transfer_evidence():
    history = _extract_course_attempts(
        "CS101 Transfer T 3\nCS102 Lab A 0.5 Spring 2025\n"
        "CS103 Research IP (12)\nCS104 Repeated B 0 2025 Winter"
    )
    assert len(history) == 4
    assert history[0].grade == "T" and history[0].term is None
    assert history[1].credits == 0.5 and history[1].term == "Spring 2025"
    assert history[2].status == "in_progress" and history[2].credits == 12
    assert history[3].term == "2025 Winter" and history[3].earns_credit is False


def test_requirement_and_title_fragments_do_not_become_passed_attempts():
    history = _extract_course_attempts(
        "Still needed: 1 Class in CS 101 or CS 102\n"
        "CS101 Intro to A and B 3 2025 Fall\n"
        "CS102 Synthetic FAIL 3 2025 Fall\nCS103 Title ABC 3 2025 Fall"
    )
    # A complete row with a final B is valid, but suffixes of unknown grade
    # tokens must never be treated as letter grades.
    assert [a.course_code for a in history if a.earns_credit] == ["CS101"]
    assert [a.grade for a in history] == ["B", "FAIL", "ABC"]


def test_columns_cannot_assign_a_neighbors_passing_grade_to_a_failed_course():
    history = _extract_course_attempts(
        "CS101 Intro F 3 2024 Fall     CS102 Lab A 1 2025 Spring\n"
        "Requirement heading     CS103 Intro W 3 2025 Fall\n"
        "CS104 Intro F 3 2025 Fall CS105 Lab A 1 2025 Fall"
    )
    assert [(a.course_code, a.grade) for a in history] == [("CS101", "F"), ("CS102", "A"), ("CS103", "W")]
    assert [a.course_code for a in history if a.earns_credit] == ["CS102"]
    assert history[0].source.line == history[1].source.line == 1


def test_posted_status_cannot_override_grade_and_legacy_history_stays_unknown():
    attempt = CourseAttempt.model_validate({
        "course_code": "cs 101", "grade": "f", "credits": 3,
        "status": "passed", "earns_credit": True,
    })
    assert attempt.course_code == "CS101"
    assert attempt.status == "failed" and attempt.earns_credit is False
    legacy = ParsedDegree(completed_courses=["CS101"])
    assert legacy.course_attempts is None
    assert legacy.completed_courses == ["CS101"]
    empty = ParsedDegree(completed_courses=["CS101"], course_attempts=[])
    assert empty.completed_courses == []
    assert CourseAttempt(course_code="CS101", grade="A").earns_credit is None


@pytest.mark.parametrize("changes", [
    {"course_code": "CS1XX"}, {"grade": " "}, {"grade": 4},
    {"credits": -1}, {"credits": True}, {"credits": "3"},
    {"credits": float("inf")}, {"term": " "},
])
def test_invalid_attempt_metadata_is_rejected(changes):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CourseAttempt.model_validate({"course_code": "CS101", "grade": "A", **changes})


@pytest.mark.parametrize("grade,credits", [("F", 3), ("W", 3), ("I", 3), ("UNKNOWN", 3), ("A", 0)])
def test_noncredit_attempt_does_not_remove_required_course_from_actual_plan(grade, credits):
    import asyncio
    from src.schemas.plan import PlanPreferences
    from src.services.plan import generate_plan
    from tests.plan.test_planner import _make_mock_session

    degree = validate_parsed_degree(ParsedDegree(
        majors=["Computer Science"], credits_remaining=3,
        completed_courses=["CS101"],
        course_attempts=[CourseAttempt(course_code="CS101", grade=grade, credits=credits)],
        still_needed=[StillNeededItem(requirement="Intro", options=["CS101"])],
    ))
    plan = asyncio.run(generate_plan(degree, PlanPreferences(), _make_mock_session()))
    assert "CS101" in [course.course_code for semester in plan.semesters for course in semester.courses]
