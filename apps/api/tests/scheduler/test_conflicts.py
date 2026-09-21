from __future__ import annotations
from datetime import time

from src.scheduler.models import CommuterOptions, MeetingSlot, SectionSlot
from src.scheduler.conflicts import (
    passes_commuter_filters,
    sections_conflict,
    validate_schedule,
)
from src.scheduler.time_utils import intervals_overlap


# ── Helpers ───────────────────────────────────────────────────────────────────

def t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))

def meeting(days: str, start: str, end: str) -> MeetingSlot:
    return MeetingSlot("CRN1", "202690", days, t(start), t(end))

def async_meeting() -> MeetingSlot:
    return MeetingSlot("CRN1", "202690", None, None, None)

def section(crn: str, course: str, meetings: list[MeetingSlot]) -> SectionSlot:
    return SectionSlot(crn=crn, term="202690", course_code=course,
                       professor_name=None, total_seats=30, open_seats=10,
                       meetings=meetings)


# ── TestIntervalsOverlap ──────────────────────────────────────────────────────

class TestIntervalsOverlap:
    """Verify the half-open interval overlap predicate on integers."""


    def test_one_minute_overlap_is_conflict(self):
        """Ends 711, starts 710 — one minute overlap."""
        assert intervals_overlap((600, 711), (710, 800)) is True


# ── TestSectionsConflict ──────────────────────────────────────────────────────

class TestSectionsConflict:
    """
    Verify conflict detection across full SectionSlots with multi-meeting patterns.
    These are the tests that would have caught the original data model bug.
    """


    def test_back_to_back_no_conflict(self):
        """
        CS101 ends 11:20, CS201 starts 11:20 — valid back-to-back.
        MOW: (1440+600, 1440+710) vs (1440+710, 1440+800) → 710 < 710 is False → no conflict.
        """
        a = section("A", "CS101", [meeting("TR", "10:00", "11:20")])
        b = section("B", "CS201", [meeting("TR", "11:20", "12:50")])
        assert not sections_conflict(a, b)

    def test_one_minute_overlap_is_conflict(self):
        a = section("A", "CS101", [meeting("TR", "10:00", "11:21")])
        b = section("B", "CS201", [meeting("TR", "11:20", "12:50")])
        assert sections_conflict(a, b)

    def test_async_vs_timed_no_conflict(self):
        """Online section must never conflict with any in-person section."""
        async_s   = section("A", "CS101", [async_meeting()])
        in_person = section("B", "CS201", [meeting("MWF", "08:00", "22:00")])
        assert not sections_conflict(async_s, in_person)


    def test_math340_lab_conflict_THE_regression_test(self):
        """
        THE regression test for the original data model bug.

        Old flat model stored MATH340 as: days='TRF', start=10:00, end=11:20
        What Banner actually reported:
          meetingsFaculty[0]: TR  10:00–11:20  (lecture)
          meetingsFaculty[1]: F   14:00–16:50  (lab)

        A student adding HIST301 (F 14:00–15:15) should see a CONFLICT.

        Under the old model: checking TRF 10:00–11:20 vs F 14:00–15:15
          → Friday shared ✓, but 14:00 < 11:20 ✗ → no conflict reported. WRONG.

        Under the new model: MATH340 expands to three MOW intervals:
          Tuesday 10:00–11:20, Thursday 10:00–11:20, Friday 14:00–16:50.
        HIST301 expands to: Friday 14:00–15:15.
        Friday 14:00–16:50 vs Friday 14:00–15:15 → overlap. CORRECT.
        """
        math340 = section("12345", "MATH340", [
            meeting("TR", "10:00", "11:20"),   # lecture
            meeting("F",  "14:00", "16:50"),   # lab
        ])
        hist301 = section("67890", "HIST301", [
            meeting("F", "14:00", "15:15"),
        ])
        assert sections_conflict(math340, hist301), \
            "F lab at 14:00 must conflict with HIST301 at 14:00"


    def test_three_pattern_section_catches_third_conflict(self):
        """
        A section with lecture + lab + recitation (3 patterns).
        Another course that only conflicts with the recitation must still be caught.
        """
        complex_section = section("X", "ECE291", [
            meeting("TR",  "10:00", "11:20"),   # lecture
            meeting("F",   "14:00", "16:50"),   # lab
            meeting("W",   "12:00", "12:50"),   # recitation
        ])
        conflicts_only_on_wednesday = section("Y", "CS201", [
            meeting("W", "12:00", "12:50"),
        ])
        assert sections_conflict(complex_section, conflicts_only_on_wednesday)


# ── TestPassesCommuterFilters ─────────────────────────────────────────────────

class TestPassesCommuterFilters:
    """
    Verify commuter constraints applied across ALL meetings of a section.
    The critical insight: a section fails if ANY single meeting violates a constraint.
    """


    def test_blocked_friday_catches_friday_lab(self):
        """
        A commuter who blocks Friday must not receive MATH340 even though its
        TR lecture starts at 10am — the F lab trips the constraint.
        """
        math340 = section("12345", "MATH340", [
            meeting("TR", "10:00", "11:20"),
            meeting("F",  "14:00", "16:50"),
        ])
        assert not passes_commuter_filters(math340, CommuterOptions(blocked_days=["F"]))


    def test_earliest_start_applies_to_all_meetings(self):
        """
        Commuter says 'not before 10:00'. MATH340 TR lecture starts at 10:00 (ok),
        but the F lab starts at 08:00 (violation). Section must be excluded.
        """
        math340 = section("12345", "MATH340", [
            MeetingSlot("12345", "202690", "TR", t("10:00"), t("11:20")),
            MeetingSlot("12345", "202690", "F",  t("08:00"), t("10:50")),
        ])
        opts = CommuterOptions(earliest_start="10:00")
        assert not passes_commuter_filters(math340, opts)


    def test_latest_end_violation_on_second_meeting(self):
        """Lab ends at 18:00, commuter cutoff is 17:00 → excluded."""
        s = section("A", "CS291", [
            meeting("TR", "10:00", "11:20"),
            meeting("F",  "15:00", "18:00"),
        ])
        opts = CommuterOptions(latest_end="17:00")
        assert not passes_commuter_filters(s, opts)

    def test_async_passes_all_time_constraints(self):
        """Async/online section has no times → always passes time-bound constraints."""
        s = section("A", "CS101", [async_meeting()])
        opts = CommuterOptions(
            blocked_days=["M", "T", "W", "R", "F"],
            earliest_start="12:00",
            latest_end="12:30",
        )
        assert passes_commuter_filters(s, opts)


# ── TestValidateSchedule ──────────────────────────────────────────────────────

class TestValidateSchedule:
    """The post-solve safety net — must catch any conflict that slips through."""


    def test_conflicting_schedule_returns_violation_string(self):
        a = section("A", "CS101", [meeting("MW", "10:00", "11:20")])
        b = section("B", "CS201", [meeting("MW", "10:00", "11:20")])
        violations = validate_schedule([a, b])
        assert len(violations) == 1
        assert "CS101" in violations[0]
        assert "CS201" in violations[0]
