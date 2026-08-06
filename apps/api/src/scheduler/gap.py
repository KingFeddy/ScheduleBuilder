from __future__ import annotations
from collections import defaultdict

from .models import SectionSlot


def _timed_sessions_by_day(sections: list[SectionSlot]) -> dict[str, list[tuple[int, int]]]:
    """
    Group every timed meeting's (start, end) minute-of-day interval by
    weekday. Shared by compute_gap_minutes and compute_gap_count so both
    walk the exact same per-day session list — only the reduction differs
    (sum of gap sizes vs. count of gap occurrences).
    """
    day_sessions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for section in sections:
        for meeting in section.meetings:
            if meeting.start_time is None or not meeting.days:
                continue
            start = meeting.start_time.hour * 60 + meeting.start_time.minute
            end   = meeting.end_time.hour * 60 + meeting.end_time.minute
            for day in meeting.days:
                if day in "MTWRF":
                    day_sessions[day].append((start, end))
    return day_sessions


def compute_gap_minutes(sections: list[SectionSlot]) -> int:
    """
    Total waiting-time minutes across all campus days.

    For each day: collect all timed class blocks, sort by start time, then sum
    the gaps between consecutive blocks. Back-to-back (gap == 0) and async
    meetings (no times) are both excluded from the total.

    Example — MATH340 (TR 10-11:20, F lab 14-16:50) + CS101 (F 12-13):
      Tuesday:  one block → 0 gap
      Thursday: one block → 0 gap
      Friday:   [(720,780), (840,1010)] sorted → gap = 840-780 = 60
      Total: 60 min
    """
    total = 0
    for sessions in _timed_sessions_by_day(sections).values():
        sessions.sort()
        for i in range(1, len(sessions)):
            gap = sessions[i][0] - sessions[i - 1][1]
            if gap > 0:
                total += gap
    return total


def compute_gap_count(sections: list[SectionSlot]) -> int:
    """
    Number of distinct gap occurrences across all campus days — how many
    times a student's day is broken up by dead time, not how long the dead
    time adds up to. Same per-day session grouping as compute_gap_minutes;
    counts gap>0 occurrences instead of summing gap size.

    Example — MATH340 (TR 10-11:20, F lab 14-16:50) + CS101 (F 12-13):
      Tuesday:  one block → no gap
      Thursday: one block → no gap
      Friday:   [(720,780), (840,1010)] sorted → one gap (840-780=60 > 0)
      Total: 1 gap occurrence
    """
    count = 0
    for sessions in _timed_sessions_by_day(sections).values():
        sessions.sort()
        for i in range(1, len(sessions)):
            gap = sessions[i][0] - sessions[i - 1][1]
            if gap > 0:
                count += 1
    return count


def compute_campus_days(sections: list[SectionSlot]) -> int:
    """Number of distinct weekdays with at least one in-person (timed) meeting."""
    days_with_class: set[str] = set()
    for section in sections:
        for meeting in section.meetings:
            if meeting.start_time is not None and meeting.days:
                days_with_class.update(c for c in meeting.days if c in "MTWRF")
    return len(days_with_class)
