from __future__ import annotations
from datetime import time
import pytest

from src.scrapers.banner import _parse_meeting_pattern


def banner_pattern(
    monday=False, tuesday=False, wednesday=False, thursday=False, friday=False,
    saturday=False,
    begin_time=None, end_time=None, building="", room=""
) -> dict:
    return {
        "meetingTime": {
            "monday": monday, "tuesday": tuesday, "wednesday": wednesday,
            "thursday": thursday, "friday": friday, "saturday": saturday,
            "beginTime": begin_time, "endTime": end_time,
        },
        "building": building,
        "room": room,
    }


class TestParseMeetingPattern:

    def test_mw_lecture_parses_days_and_times(self):
        pattern = banner_pattern(monday=True, wednesday=True,
                                  begin_time="1000", end_time="1115")
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "MW"
        assert start == time(10, 0)
        assert end == time(11, 15)

    def test_tr_lecture(self):
        pattern = banner_pattern(tuesday=True, thursday=True,
                                  begin_time="1400", end_time="1515")
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "TR"
        assert start == time(14, 0)
        assert end == time(15, 15)

    def test_friday_lab(self):
        pattern = banner_pattern(friday=True, begin_time="1400", end_time="1650")
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "F"
        assert start == time(14, 0)
        assert end == time(16, 50)

    def test_saturday_only_lab(self):
        """A Saturday-only section must parse to days == 'S', not be silently dropped."""
        pattern = banner_pattern(saturday=True, begin_time="0900", end_time="1150")
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "S"
        assert start == time(9, 0)
        assert end == time(11, 50)

    def test_monday_and_saturday_mixed_pattern(self):
        """
        A section meeting both Monday and Saturday must keep both days — this
        is the exact failure mode that made Saturday classes invisible: the
        Saturday portion of a mixed pattern was silently dropped, so a real
        Monday+Saturday section was stored as Monday-only.
        """
        pattern = banner_pattern(monday=True, saturday=True,
                                  begin_time="1000", end_time="1120")
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "MS"

    def test_async_pattern_returns_all_none(self):
        """No days, no times → fully async section."""
        pattern = banner_pattern()
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days is None
        assert start is None
        assert end is None

    def test_no_times_returns_none_times(self):
        """Days present but no beginTime/endTime → treat as TBA."""
        pattern = banner_pattern(monday=True, wednesday=True)
        days, start, end, location = _parse_meeting_pattern(pattern)
        assert days == "MW"
        assert start is None
        assert end is None

    def test_location_assembled_from_building_and_room(self):
        pattern = banner_pattern(monday=True, begin_time="0900", end_time="0950",
                                  building="KUPF", room="202")
        _, _, _, location = _parse_meeting_pattern(pattern)
        assert location == "KUPF 202"

    def test_empty_building_and_room_gives_none_location(self):
        pattern = banner_pattern(monday=True, begin_time="0900", end_time="0950")
        _, _, _, location = _parse_meeting_pattern(pattern)
        assert location is None

    def test_early_morning_time_parsed_correctly(self):
        """'0830' must produce time(8, 30), not time(0, 830)."""
        pattern = banner_pattern(monday=True, begin_time="0830", end_time="0920")
        _, start, end, _ = _parse_meeting_pattern(pattern)
        assert start == time(8, 30)
        assert end == time(9, 20)

    def test_day_order_is_mtwrf(self):
        """Days must always appear in calendar order regardless of Banner key order."""
        pattern = banner_pattern(friday=True, monday=True, wednesday=True,
                                  begin_time="1000", end_time="1050")
        days, _, _, _ = _parse_meeting_pattern(pattern)
        assert days == "MWF"


# ── TestCleanCourseTitle ────────────────────────────────────────────────────

class TestCleanCourseTitle:
    """
    Every case here is a real title confirmed against live Banner data
    during design — see
    docs/superpowers/specs/2026-08-07-course-title-cleanup-design.md.
    """

    def test_html_entity_unescaped(self):
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("Elect &amp; Comp Engr Tech") == "Elect & Comp Engr Tech"

    def test_honors_suffix_with_space_before_dash_stripped(self):
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("Math Of Fin Derivatives I - HONORS") == "Math Of Fin Derivatives I"

    def test_honors_suffix_with_no_space_before_dash_stripped(self):
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("STATISTICS CAPSTONE I- Honors") == "Statistics Capstone I"

    def test_leading_whitespace_and_all_caps_honors_title_cleaned(self):
        """The exact CS351 Honors section title, confirmed live during design."""
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title(" INTRODUCTION TO CYBERSECURITY - HONORS") == "Introduction To Cybersecurity"

    def test_all_caps_special_topics_title_case_cased(self):
        """
        Not Honors-related — NJIT's Special Topics (ST:) courses are also
        stored all-caps by Banner. Documents the accepted acronym-casing
        imperfection directly: "ST" and "AI" both come out with only their
        first letter capitalized, since plain title-casing can't tell an
        acronym from a regular word.
        """
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("ST: PHYSICAL AI") == "St: Physical Ai"

    def test_already_clean_title_passes_through_unchanged(self):
        """A normally-cased title must not be touched — 'to' must stay lowercase."""
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("Introduction to Cybersecurity") == "Introduction to Cybersecurity"

    def test_symbol_only_title_not_mistaken_for_uppercase(self):
        """
        A title with no alphabetic characters at all (e.g. a placeholder or
        degenerate stub) must not trigger .title() — upper() and lower()
        are both identity for a string with no letters, so a naive
        equality check against just .upper() alone would misfire here.
        Pins the `cleaned != cleaned.lower()` half of the guard.
        """
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("101") == "101"
        assert _clean_course_title("--") == "--"

    def test_empty_string_input_returns_empty_string(self):
        """No exception, no crash — an empty title is a degenerate but valid input."""
        from src.scrapers.banner import _clean_course_title
        assert _clean_course_title("") == ""
