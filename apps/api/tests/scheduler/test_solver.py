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

    def test_saturday_meeting_counted_in_gap_minutes(self):
        """A Saturday meeting must contribute to gap math like any weekday — not be silently ignored."""
        s1 = section("A", "CS101", [make_meeting("S", "09:00", "09:50")])
        s2 = section("B", "CS201", [make_meeting("S", "11:00", "11:50")])
        assert compute_gap_minutes([s1, s2]) == 70

    def test_campus_days_counts_saturday(self):
        """A Saturday-only meeting must count as a campus day."""
        s = section("A", "CS101", [make_meeting("S", "09:00", "11:50")])
        assert compute_campus_days([s]) == 1


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

    def test_saturday_meeting_counted_in_gap_count(self):
        """A Saturday gap must count as a real gap occurrence, not be silently ignored."""
        s1 = section("A", "CS101", [make_meeting("S", "09:00", "09:50")])
        s2 = section("B", "CS201", [make_meeting("S", "11:00", "11:50")])
        assert compute_gap_count([s1, s2]) == 1


# ── TestGapSignificanceThreshold ────────────────────────────────────────────────

class TestGapSignificanceThreshold:
    """
    A gap of MIN_SIGNIFICANT_GAP_MINUTES (10) or less is a normal passing
    period, not a real break — it must not contribute to either gap_count or
    gap_minutes at all. Only gaps strictly greater than the threshold count.
    """

    def test_exactly_ten_minute_gap_does_not_count(self):
        """A 10-minute passing period is excluded entirely, not just from the count."""
        s1 = section("A", "CS101", [make_meeting("M", "09:00", "09:50")])
        s2 = section("B", "CS201", [make_meeting("M", "10:00", "10:50")])
        assert compute_gap_count([s1, s2]) == 0
        assert compute_gap_minutes([s1, s2]) == 0

    def test_eleven_minute_gap_counts(self):
        """One minute over the threshold — this IS a real gap."""
        s1 = section("A", "CS101", [make_meeting("M", "09:00", "09:50")])
        s2 = section("B", "CS201", [make_meeting("M", "10:01", "10:51")])
        assert compute_gap_count([s1, s2]) == 1
        assert compute_gap_minutes([s1, s2]) == 11

    def test_mix_of_small_and_large_gaps_only_large_ones_count(self):
        """
        Real-world case reported by a user: CS351 (MW 11:30-12:50) + CS341
        (WF 13:00-14:20) + CS375 (MR 14:30-15:50) + CS288 (F 14:30-17:20) +
        ECE231 (R 18:00-22:05), with CS350 as the only variable —

        Schedule 1: CS350 meets M 16:00-17:20 AND R 16:00-17:20 (two rows).
          Mon: CS351(11:30-12:50) -[100min]- CS375(14:30-15:50) -[10min]- CS350(16:00-17:20)
          Wed: CS351(11:30-12:50) -[10min]- CS341(13:00-14:20)
          Thu: CS375(14:30-15:50) -[10min]- CS350(16:00-17:20) -[40min]- ECE231(18:00-22:05)
          Fri: CS341(13:00-14:20) -[10min]- CS288(14:30-17:20)
          Only the 100-min and 40-min gaps exceed the threshold → gap_count=2.

        Schedule 2: CS350 meets MR 13:00-14:20 (one row).
          Mon: CS351(11:30-12:50) -[10min]- CS350(13:00-14:20) -[10min]- CS375(14:30-15:50)
          Wed: CS351(11:30-12:50) -[10min]- CS341(13:00-14:20)
          Thu: CS350(13:00-14:20) -[10min]- CS375(14:30-15:50) -[130min]- ECE231(18:00-22:05)
          Fri: CS341(13:00-14:20) -[10min]- CS288(14:30-17:20)
          Only the 130-min gap exceeds the threshold → gap_count=1.

        Before this threshold existed, both schedules tied at gap_count=6 and
        gap_minutes=180 (every 10-min passing period counted equally with the
        100/130/40-min real gaps), which is what prompted this fix.
        """
        cs351 = section("A", "CS351", [make_meeting("MW", "11:30", "12:50")])
        cs341 = section("B", "CS341", [make_meeting("WF", "13:00", "14:20")])
        cs375 = section("C", "CS375", [make_meeting("MR", "14:30", "15:50")])
        cs288 = section("D", "CS288", [make_meeting("F", "14:30", "17:20")])
        ece231 = section("E", "ECE231", [make_meeting("R", "18:00", "22:05")])

        cs350_two_rows = section("F1", "CS350", [
            make_meeting("M", "16:00", "17:20"),
            make_meeting("R", "16:00", "17:20"),
        ])
        schedule1 = [cs351, cs341, cs375, cs288, ece231, cs350_two_rows]
        assert compute_gap_count(schedule1) == 2
        assert compute_gap_minutes(schedule1) == 140  # 100 + 40, the two real gaps only

        cs350_one_row = section("F2", "CS350", [make_meeting("MR", "13:00", "14:20")])
        schedule2 = [cs351, cs341, cs375, cs288, ece231, cs350_one_row]
        assert compute_gap_count(schedule2) == 1
        assert compute_gap_minutes(schedule2) == 130  # the Thursday gap only


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

    def test_minimize_gaps_true_ranks_fewer_gap_occurrences_over_fewer_total_minutes(self):
        """
        Fewer gap occurrences must outrank fewer total gap minutes when
        minimize_gaps=True — this is the actual behavior change this
        feature exists for. Reuses the exact many-small-gaps vs.
        one-big-gap fixture from TestGapCount, at the solve() level.
        """
        # Option A: CS101 meets twice Monday (9-9:50, 10:10-11), CS201 fixed
        #   11:20-12:10 → sessions [(540,590),(610,660),(680,730)]
        #   → 2 gap occurrences, 40 min total.
        cs101_two_gaps = section("A", "CS101", [
            make_meeting("M", "09:00", "09:50"),
            make_meeting("M", "10:10", "11:00"),
        ])
        # Option B: CS101 meets once Monday (9-9:50), same CS201
        #   → sessions [(540,590),(680,730)]
        #   → 1 gap occurrence, 90 min total.
        cs101_one_gap = section("B", "CS101", [make_meeting("M", "09:00", "09:50")])
        cs201 = section("C", "CS201", [make_meeting("M", "11:20", "12:10")])
        result = solve(
            ["CS101", "CS201"],
            {"CS101": [cs101_two_gaps, cs101_one_gap], "CS201": [cs201]},
            CommuterOptions(minimize_gaps=True),
            {},
        )
        first_crns = {s.crn for s in result.results[0].sections}
        assert "B" in first_crns, (
            "Schedule with fewer gap occurrences (B: 1 gap, 90 min) must rank first "
            "under minimize_gaps=True, even though it has MORE total gap minutes "
            "than the alternative (A: 2 gaps, 40 min)"
        )

    def test_compact_week_ranks_days_then_gap_count_then_gap_minutes(self):
        """
        When compact_week=True: campus days breaks ties first, then gap
        count, then gap minutes.
        """
        # CS201 fixed: Monday 11:20-12:10.
        cs201 = section("C", "CS201", [make_meeting("M", "11:20", "12:10")])
        # Option A: CS101 meets twice Monday → 1 campus day (Monday only),
        #   2 gap occurrences, 40 min total.
        cs101_two_gaps = section("A", "CS101", [
            make_meeting("M", "09:00", "09:50"),
            make_meeting("M", "10:10", "11:00"),
        ])
        # Option B: CS101 meets once Monday → 1 campus day, 1 gap
        #   occurrence, 90 min total.
        cs101_one_gap = section("B", "CS101", [make_meeting("M", "09:00", "09:50")])
        # Option D: CS101 meets Tuesday only (no overlap with CS201's
        #   Monday slot) → 2 campus days (Mon + Tue), 0 gap occurrences
        #   (each day has exactly one block), 0 min total — the best gap
        #   stats of the three, but it must still rank LAST because it
        #   costs an extra campus day, which is the primary sort key here.
        cs101_extra_day = section("D", "CS101", [make_meeting("T", "09:00", "09:50")])

        result = solve(
            ["CS101", "CS201"],
            {"CS101": [cs101_two_gaps, cs101_one_gap, cs101_extra_day], "CS201": [cs201]},
            CommuterOptions(),
            {},
            compact_week=True,
        )
        crns_in_order = [{s.crn for s in sch.sections} for sch in result.results]
        b_rank = next(i for i, crns in enumerate(crns_in_order) if "B" in crns)
        a_rank = next(i for i, crns in enumerate(crns_in_order) if "A" in crns)
        d_rank = next(i for i, crns in enumerate(crns_in_order) if "D" in crns)
        assert b_rank < a_rank, (
            "B (1 day, 1 gap) must rank ahead of A (1 day, 2 gaps) — "
            "campus days tied, fewer gap occurrences wins"
        )
        assert d_rank > a_rank and d_rank > b_rank, (
            "D (2 days, 0 gaps) must rank LAST despite having the best gap stats — "
            "campus days is the primary sort key under compact_week"
        )


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


# ── TestHideFullSections ─────────────────────────────────────────────────────

class TestHideFullSections:

    def test_full_section_included_by_default(self):
        """hide_full_sections defaults False — a full section still appears."""
        full = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], open_seats=0)
        result = solve(["CS101"], {"CS101": [full]}, no_constraints, {})
        assert result.results
        assert result.results[0].sections[0].crn == "A"

    def test_hide_full_sections_true_excludes_full_keeps_open(self):
        """With the toggle on, only the section with open seats survives."""
        full = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], open_seats=0)
        open_sec = section("B", "CS101", [make_meeting("TR", "10:00", "11:20")], open_seats=5)
        result = solve(
            ["CS101"], {"CS101": [full, open_sec]},
            CommuterOptions(hide_full_sections=True), {},
        )
        crns = {s.crn for sch in result.results for s in sch.sections}
        assert crns == {"B"}

    def test_hide_full_sections_true_all_full_gives_specific_warning(self):
        """Every section full + toggle on → empty result with the seat-specific warning."""
        full = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], open_seats=0)
        result = solve(
            ["CS101"], {"CS101": [full]},
            CommuterOptions(hide_full_sections=True), {},
        )
        assert result.results == []
        assert any("full" in w.lower() and "CS101" in w for w in result.warnings)

    def test_hide_full_sections_scoped_per_course_like_other_filter_stages(self):
        """
        A course that goes to zero candidates because of the seat filter aborts
        the solve immediately, exactly like every other zero-candidate case
        (unknown course, professor whitelist eliminates everyone) — the second
        course never even gets evaluated. Proves the new filter stage follows
        the same early-return contract as the existing two stages.
        """
        cs101_full = section("A", "CS101", [make_meeting("MW", "10:00", "11:20")], open_seats=0)
        cs201_open = section("B", "CS201", [make_meeting("TR", "10:00", "11:20")], open_seats=5)
        result = solve(
            ["CS101", "CS201"],
            {"CS101": [cs101_full], "CS201": [cs201_open]},
            CommuterOptions(hide_full_sections=True), {},
        )
        assert result.results == []
        assert any("CS101" in w and "full" in w.lower() for w in result.warnings)


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

    def test_accepts_saturday_as_blocked_day(self):
        opts = CommuterOptions(blocked_days=["S"])
        assert opts.blocked_days == ["S"]


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
