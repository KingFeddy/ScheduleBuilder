from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time, datetime
from typing import Optional


@dataclass(frozen=True)
class MeetingSlot:
    """One time block a section occupies. Immutable."""
    crn:        str
    term:       str
    days:       Optional[str]        # e.g. 'TR', 'F', 'MWF'; None = async
    start_time: Optional[time]       # None = async
    end_time:   Optional[time]       # None = async
    location:   Optional[str] = None


@dataclass
class SectionSlot:
    """A schedulable section with all its meeting patterns."""
    crn:            str
    term:           str
    course_code:    str
    professor_name: Optional[str]
    total_seats:    int
    open_seats:     int
    meetings:       list[MeetingSlot] = field(default_factory=list)
    scraped_at:     Optional[datetime] = None
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    topic: Optional[str] = None

    @property
    def is_async(self) -> bool:
        """True if ALL meetings are async (no timed meetings)."""
        return all(m.start_time is None for m in self.meetings) or len(self.meetings) == 0


@dataclass
class CommuterOptions:
    """Commuter constraint preferences for schedule filtering."""
    blocked_days:   Optional[list[str]] = None   # e.g. ['M', 'F']
    earliest_start: Optional[str] = None          # 'HH:MM'
    latest_end:     Optional[str] = None          # 'HH:MM'
