from __future__ import annotations
from datetime import time, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.services.courses import load_sections_with_meetings
from src.scheduler.models import MeetingSlot, SectionSlot


def make_section_row(crn, course_code, prof=None, total=30, open_seats=10, section_number="001"):
    return {
        "crn": crn, "term": "202690", "course_code": course_code,
        "professor_name": prof, "total_seats": total,
        "open_seats": open_seats, "scraped_at": datetime(2026, 1, 1),
        "section_number": section_number,
    }

def make_meeting_row(crn, days, start, end, location=None):
    return {
        "crn": crn, "term": "202690",
        "days": days, "start_time": start, "end_time": end,
        "location": location,
    }

def mock_session_with(section_rows, meeting_rows):
    """Build a mock AsyncSession that returns the given rows for two execute calls."""
    def make_result(rows):
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        make_result(section_rows),
        make_result(meeting_rows),
    ])
    return session


class TestLoadSectionsWithMeetings:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_sections(self):
        """No sections → two queries not run, returns empty lists per course."""
        # When sections query returns nothing, meetings query is skipped.
        result_mock = MagicMock()
        result_mock.mappings.return_value.all.return_value = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)

        result = await load_sections_with_meetings(session, ["CS101"], "202690")

        assert result == {"CS101": []}
        # Only one query (sections) should fire — no CRNs to fetch meetings for
        assert session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_single_section_single_meeting(self):
        section_rows = [make_section_row("11111", "CS101")]
        meeting_rows = [make_meeting_row("11111", "MWF", time(10, 0), time(10, 50))]
        session = mock_session_with(section_rows, meeting_rows)

        result = await load_sections_with_meetings(session, ["CS101"], "202690")

        assert "CS101" in result
        sections = result["CS101"]
        assert len(sections) == 1
        slot = sections[0]
        assert slot.crn == "11111"
        assert len(slot.meetings) == 1
        assert slot.meetings[0].days == "MWF"

    @pytest.mark.asyncio
    async def test_multi_meeting_section_groups_correctly(self):
        """MATH340 with TR lecture + F lab must produce one SectionSlot with two meetings."""
        section_rows = [make_section_row("12345", "MATH340")]
        meeting_rows = [
            make_meeting_row("12345", "TR", time(10, 0), time(11, 20)),
            make_meeting_row("12345", "F",  time(14, 0), time(16, 50)),
        ]
        session = mock_session_with(section_rows, meeting_rows)

        result = await load_sections_with_meetings(session, ["MATH340"], "202690")

        sections = result["MATH340"]
        assert len(sections) == 1
        slot = sections[0]
        assert len(slot.meetings) == 2
        days = {m.days for m in slot.meetings}
        assert days == {"TR", "F"}

    @pytest.mark.asyncio
    async def test_two_courses_two_queries_total(self):
        """Two courses must still result in exactly two DB queries — no N+1."""
        section_rows = [
            make_section_row("11111", "CS101"),
            make_section_row("22222", "CS201"),
        ]
        meeting_rows = [
            make_meeting_row("11111", "MWF", time(10, 0), time(10, 50)),
            make_meeting_row("22222", "TR",  time(14, 0), time(15, 15)),
        ]
        session = mock_session_with(section_rows, meeting_rows)

        result = await load_sections_with_meetings(session, ["CS101", "CS201"], "202690")

        assert session.execute.call_count == 2
        assert len(result["CS101"]) == 1
        assert len(result["CS201"]) == 1

    @pytest.mark.asyncio
    async def test_section_with_no_meetings_gets_empty_list(self):
        """A section with no rows in meetings table gets meetings=[]."""
        section_rows = [make_section_row("99999", "CS999")]
        meeting_rows = []   # no meetings for this CRN
        session = mock_session_with(section_rows, meeting_rows)

        result = await load_sections_with_meetings(session, ["CS999"], "202690")

        slot = result["CS999"][0]
        assert slot.meetings == []
        assert slot.is_async is True

    @pytest.mark.asyncio
    async def test_requested_course_not_in_db_returns_empty_list(self):
        """Courses with no sections in DB return an empty list, not KeyError."""
        section_rows = [make_section_row("11111", "CS101")]
        meeting_rows = [make_meeting_row("11111", "MWF", time(10, 0), time(10, 50))]
        session = mock_session_with(section_rows, meeting_rows)

        result = await load_sections_with_meetings(session, ["CS101", "CS999"], "202690")

        assert result["CS999"] == []
