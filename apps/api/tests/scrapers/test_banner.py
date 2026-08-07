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
