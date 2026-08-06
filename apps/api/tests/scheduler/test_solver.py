from __future__ import annotations
from datetime import time
import pytest

from src.scheduler.models import MeetingSlot, SectionSlot
from src.scheduler.solver import solve, _find_impossible_pair, _professor_matches_whitelist
from src.scheduler.gap import compute_gap_minutes, compute_gap_count, compute_campus_days
from src.schemas.schedule import CommuterOptions, SolveRequest


# ── Helpers ───────────────────────────────────────────────────────────────────

def t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def make_meeting(days: str, start: str, end: str) -> MeetingSlot:
    return MeetingSlot("CRN", "202690", days, t(start), t(end))


def make_async_meeting() -> MeetingSlot:
    return MeetingSlot("CRN", "202690", None, None, None)


def section(
    crn: str,
    course: str,
    meetings: list,
    prof: str | None = None,
    open_seats: int = 10,
) -> SectionSlot:
    return SectionSlot(
        crn=crn, term="202690", course_code=course,
        professor_name=prof, total_seats=30,
        open_seats=open_seats, meetings=meetings,
    )


no_constraints = CommuterOptions()


# ── TestGapCalculation ────────────────────────────────────────────────────────

class TestGapCalculation:
    """Verify gap minutes using the consecutive-gaps approach across multi-meeting sections."""

    def test_single_block_per_day_zero_gap(self):
        """One class per day: no waiting → 0 gap."""
        s = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        assert compute_gap_minutes([s]) == 0

    def test_back_to_back_zero_gap(self):
        """Classes that end and start at the same minute = 0 gap."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "11:20", "12:50")])
        assert compute_gap_minutes([s1, s2]) == 0

    def test_sixty_minute_gap_two_days(self):
        """MW 10-11:20 + MW 12:20-13:40 → 60 min gap × 2 days = 120."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "12:20", "13:40")])
        assert compute_gap_minutes([s1, s2]) == 120

    def test_gap_multi_meeting_section_correct_per_day(self):
        """
        MATH340: TR lecture 10-11:20 + F lab 14:00-16:50.
        CS101: F 12:00-13:00.
        TR: only MATH340 lecture each day → 0 gap.
        F: sorted sessions [(720,780), (840,1010)] → gap = 840-780 = 60.
        Total: 60.
        """
        math340 = section("M", "MATH340", [
            make_meeting("TR", "10:00", "11:20"),
            make_meeting("F",  "14:00", "16:50"),
        ])
        cs101 = section("O", "CS101", [make_meeting("F", "12:00", "13:00")])
        assert compute_gap_minutes([math340, cs101]) == 60

    def test_async_section_contributes_zero_gap(self):
        """Async section has no times → never contributes to gap."""
        async_s = section("A", "CS101", [make_async_meeting()])
        timed_s = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])
        assert compute_gap_minutes([async_s, timed_s]) == 0

    def test_campus_days_multi_meeting_counted_correctly(self):
        """MATH340 (TR+F) spans 3 distinct campus days."""
        math340 = section("M", "MATH340", [
            make_meeting("TR", "10:00", "11:20"),
            make_meeting("F",  "14:00", "16:50"),
        ])
        assert compute_campus_days([math340]) == 3

    def test_campus_days_async_not_counted(self):
        """Async section must not inflate campus day count."""
        async_s = section("A", "CS101", [make_async_meeting()])
        timed_s = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])
        assert compute_campus_days([async_s, timed_s]) == 2


# ── TestGapCount ─────────────────────────────────────────────────────────────

class TestGapCount:
    """
    Verify gap *count* (number of occurrences, not total minutes) using the
    same fixtures TestGapCalculation uses for compute_gap_minutes, so the two
    metrics are directly comparable against identical inputs.
    """

    def test_single_block_per_day_zero_count(self):
        """One class per day: no waiting → 0 gap occurrences."""
        s = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        assert compute_gap_count([s]) == 0

    def test_back_to_back_zero_count(self):
        """Classes that end and start at the same minute = 0 gap occurrences."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "11:20", "12:50")])
        assert compute_gap_count([s1, s2]) == 0

    def test_one_gap_two_days_counts_two(self):
        """MW 10-11:20 + MW 12:20-13:40 → one gap occurrence per day × 2 days = 2."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "12:20", "13:40")])
        assert compute_gap_count([s1, s2]) == 2

    def test_gap_multi_meeting_section_counts_correctly(self):
        """
        MATH340: TR lecture 10-11:20 + F lab 14:00-16:50.
        CS101: F 12:00-13:00.
        TR: only MATH340 lecture each day → 0 gap occurrences.
        F: sorted sessions [(720,780), (840,1010)] → 1 gap occurrence.
        Total: 1.
        """
        math340 = section("M", "MATH340", [
            make_meeting("TR", "10:00", "11:20"),
            make_meeting("F",  "14:00", "16:50"),
        ])
        cs101 = section("O", "CS101", [make_meeting("F", "12:00", "13:00")])
        assert compute_gap_count([math340, cs101]) == 1

    def test_async_section_contributes_zero_count(self):
        """Async section has no times → never contributes to gap count."""
        async_s = section("A", "CS101", [make_async_meeting()])
        timed_s = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])
        assert compute_gap_count([async_s, timed_s]) == 0

    def test_more_smaller_gaps_outcounts_one_larger_gap(self):
        """
        The property this whole feature exists for: two 20-min gaps (count=2,
        40 min total) must count as MORE gaps than one 90-min gap (count=1,
        90 min total), even though the one-gap schedule has more total dead
        time. This is what makes gap count a meaningfully different ranking
        signal from gap minutes.
        """
        s1 = section("A", "CS101", [make_meeting("M", "09:00", "09:50")])
        s2 = section("B", "CS201", [make_meeting("M", "10:10", "11:00")])
        s3 = section("C", "CS301", [make_meeting("M", "11:20", "12:10")])
        many_small_gaps = [s1, s2, s3]
        assert compute_gap_count(many_small_gaps) == 2
        assert compute_gap_minutes(many_small_gaps) == 40

        s4 = section("D", "CS401", [make_meeting("M", "09:00", "09:50")])
        s5 = section("E", "CS501", [make_meeting("M", "11:20", "12:10")])
        one_big_gap = [s4, s5]
        assert compute_gap_count(one_big_gap) == 1
        assert compute_gap_minutes(one_big_gap) == 90


# ── TestSolverCorrectness ─────────────────────────────────────────────────────

class TestSolverCorrectness:

    def test_single_course_returns_all_valid_sections(self):
        """With one course, every valid section is its own schedule."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS101", [make_meeting("TR", "10:00", "11:20")])
        result = solve(["CS101"], {"CS101": [s1, s2]}, no_constraints, {})
        assert result.results
        crns = {sch.sections[0].crn for sch in result.results}
        assert "A" in crns and "B" in crns

    def test_compatible_courses_produce_schedule(self):
        """MW + TR courses share no MOW intervals → no conflict → valid schedule."""
        mw = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        tr = section("B", "CS201", [make_meeting("TR", "10:00", "11:20")])
        result = solve(["CS101", "CS201"], {"CS101": [mw], "CS201": [tr]}, no_constraints, {})
        assert len(result.results) == 1
        assert not result.warnings

    def test_impossible_combination_returns_empty_with_named_pair(self):
        """Two courses both only offered MW 10-11:20 → no valid schedule."""
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])
        result = solve(["CS101", "CS201"], {"CS101": [s1], "CS201": [s2]}, no_constraints, {})
        assert result.results == []
        assert any("CS101" in w and "CS201" in w for w in result.warnings), \
            "Warning must name the specific conflicting pair"

    def test_course_not_in_db_returns_empty_with_specific_warning(self):
        """Unknown course code → empty result with course-specific warning."""
        cs101 = section("A", "CS101", [make_meeting("MW", "10:00", "10:50")])
        result = solve(["CS101", "CS999"], {"CS101": [cs101], "CS999": []}, no_constraints, {})
        assert result.results == []
        assert any("CS999" in w for w in result.warnings)

    def test_minimize_gaps_true_ranks_lower_gap_first(self):
        """Back-to-back schedule must outrank a schedule with a large gap."""
        cs101_backtoback = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        cs101_early      = section("B", "CS101", [make_meeting("MW", "08:00", "09:20")])
        cs201            = section("C", "CS201", [make_meeting("MW", "11:30", "12:50")])
        result = solve(
            ["CS101", "CS201"],
            {"CS101": [cs101_backtoback, cs101_early], "CS201": [cs201]},
            CommuterOptions(minimize_gaps=True),
            {},
        )
        # Schedule containing A (ends 11:20, 10 min gap to CS201 at 11:30) ranks first
        # Schedule containing B (ends 09:20, ~2h10m gap) ranks second
        first_crns = {s.crn for s in result.results[0].sections}
        assert "A" in first_crns, "Back-to-back schedule must rank first when minimize_gaps=True"

    def test_minimize_gaps_false_ranks_fewer_campus_days_first(self):
        """When minimize_gaps=False, schedule with fewest campus days ranks first."""
        mwf_section = section("A", "CS101", [make_meeting("MWF", "09:00", "09:50")])
        tr_section  = section("B", "CS101", [make_meeting("TR",  "09:00", "09:50")])
        cs201       = section("C", "CS201", [make_meeting("TR",  "10:00", "11:20")])
        result = solve(
            ["CS101", "CS201"],
            {"CS101": [mwf_section, tr_section], "CS201": [cs201]},
            CommuterOptions(minimize_gaps=False),
            {},
        )
        # TR+TR = 2 campus days; MWF+TR = 4 campus days
        first_crns = {s.crn for s in result.results[0].sections}
        assert "B" in first_crns, "TR-only schedule (2 campus days) must rank first"


# ── TestProfessorWhitelist ────────────────────────────────────────────────────

class TestProfessorWhitelist:

    def test_whitelist_filters_to_named_professor_only(self):
        smith = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], prof="Dr. Smith")
        jones = section("B", "CS101", [make_meeting("TR", "10:00", "11:20")], prof="Dr. Jones")
        result = solve(["CS101"], {"CS101": [smith, jones]}, no_constraints, {"CS101": ["Dr. Smith"]})
        crns = {sch.sections[0].crn for sch in result.results}
        assert "A" in crns and "B" not in crns

    def test_whitelist_match_is_case_insensitive(self):
        assert _professor_matches_whitelist("Dr. Smith", ["dr. smith"])
        assert _professor_matches_whitelist("DR. SMITH", ["Dr. Smith"])
        assert not _professor_matches_whitelist("Dr. Jones", ["Dr. Smith"])

    def test_empty_whitelist_accepts_any_professor(self):
        assert _professor_matches_whitelist("Anyone", [])
        assert _professor_matches_whitelist(None, [])

    def test_whitelist_eliminates_all_sections_gives_named_warning(self):
        """Warning must name the missing professor, not just the course."""
        s = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], prof="Dr. Smith")
        result = solve(["CS101"], {"CS101": [s]}, no_constraints, {"CS101": ["Dr. Nobody"]})
        assert result.results == []
        assert any("Dr. Nobody" in w or "CS101" in w for w in result.warnings)


# ── TestCommuterConstraints ───────────────────────────────────────────────────

class TestCommuterConstraints:

    def test_blocked_day_excludes_multi_meeting_section(self):
        """MATH340 (TR+F lab) must be excluded when F is blocked — THE regression."""
        math340 = section("M", "MATH340", [
            make_meeting("TR", "10:00", "11:20"),
            make_meeting("F",  "14:00", "16:50"),
        ])
        result = solve(
            ["MATH340"], {"MATH340": [math340]},
            CommuterOptions(blocked_days=["F"]), {},
        )
        assert result.results == []
        assert result.warnings

    def test_async_section_passes_all_constraints(self):
        """Async section must pass even with all days blocked and a tiny time window."""
        async_s = section("A", "CS101", [make_async_meeting()])
        result = solve(
            ["CS101"], {"CS101": [async_s]},
            CommuterOptions(
                blocked_days=["M", "T", "W", "R", "F"],
                earliest_start="12:00",
                latest_end="12:30",
            ),
            {},
        )
        assert result.results


# ── TestPostSolveValidation ───────────────────────────────────────────────────

class TestPostSolveValidation:

    def test_validation_safety_net_discards_conflicts(self, monkeypatch):
        """
        Patch sections_conflict in solver.py's own namespace.
        This breaks the backtracker's pruning — it won't detect the overlap,
        so it assembles a conflicting pair.
        validate_schedule (imported from conflicts.py) still calls the real
        sections_conflict from conflicts.py's namespace and catches the conflict.
        The schedule must be discarded; result must be empty.
        """
        import src.scheduler.solver as solver_module

        mw_a = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        mw_b = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])

        monkeypatch.setattr(solver_module, "sections_conflict", lambda a, b: False)

        result = solve(
            ["CS101", "CS201"],
            {"CS101": [mw_a], "CS201": [mw_b]},
            no_constraints,
            {},
        )

        assert result.results == [], \
            "validate_schedule must discard conflicting schedules as an independent safety net"


# ── TestInputValidation ───────────────────────────────────────────────────────

class TestInputValidation:

    def test_rejects_too_many_courses(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SolveRequest(course_codes=["CS1"] * 9, term="202690")

    def test_deduplicates_course_codes(self):
        req = SolveRequest(course_codes=["CS280", "CS280", "MATH340"], term="202690")
        assert req.course_codes.count("CS280") == 1

    def test_rejects_invalid_time_format(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SolveRequest(
                course_codes=["CS280"], term="202690",
                options=CommuterOptions(earliest_start="25:00"),
            )

    def test_rejects_invalid_term(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SolveRequest(course_codes=["CS280"], term="abcdef")

    def test_accepts_valid_term_formats(self):
        for term in ["202690", "202710", "202750"]:
            req = SolveRequest(course_codes=["CS280"], term=term)
            assert req.term == term


# ── TestFindImpossiblePair ────────────────────────────────────────────────────

class TestFindImpossiblePair:

    def test_finds_conflicting_pair(self):
        s1 = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        s2 = section("B", "CS201", [make_meeting("MW", "10:00", "11:20")])
        pair = _find_impossible_pair(["CS101", "CS201"], {"CS101": [s1], "CS201": [s2]})
        assert pair == ("CS101", "CS201")

    def test_returns_none_when_compatible(self):
        mw = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        tr = section("B", "CS201", [make_meeting("TR", "10:00", "11:20")])
        pair = _find_impossible_pair(["CS101", "CS201"], {"CS101": [mw], "CS201": [tr]})
        assert pair is None


# ── TestSectionToResponseMultiMeeting ─────────────────────────────────────────

class TestSectionToResponseMultiMeeting:
    """_section_to_response must serialize every meeting a section has, not
    just the first one — the bug this fixes: a section with a Monday row
    and a separate Thursday row at the same DB CRN was silently collapsed
    to Monday-only in the API response."""

    def test_all_meetings_serialized_not_just_first(self):
        from src.scheduler.solver import _section_to_response

        s = section("91944", "CS350", [
            make_meeting("M", "16:00", "17:20"),
            make_meeting("R", "16:00", "17:20"),
        ])

        response = _section_to_response(s)

        assert len(response.meetings) == 2, "Both meeting rows must be serialized, not just the first"
        assert {m.days for m in response.meetings} == {"M", "R"}

    def test_single_meeting_section_still_works(self):
        from src.scheduler.solver import _section_to_response

        s = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")])
        response = _section_to_response(s)

        assert len(response.meetings) == 1
        assert response.meetings[0].days == "MW"
        assert response.meetings[0].start_time == "10:00"
        assert response.meetings[0].end_time == "11:20"

    def test_async_section_serializes_meeting_with_null_times(self):
        from src.scheduler.solver import _section_to_response

        s = section("B", "CS999", [make_async_meeting()])
        response = _section_to_response(s)

        assert len(response.meetings) == 1
        assert response.meetings[0].days is None
        assert response.meetings[0].start_time is None
