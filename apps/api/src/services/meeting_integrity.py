"""Distinguish explicit asynchronous meetings from incomplete scheduling data."""
from datetime import time

from ..scheduler.time_utils import DAY_OFFSETS


class IncompleteMeetingData(ValueError):
    def __init__(self):
        super().__init__(
            "Meeting data is incomplete for the selected courses. "
            "Please try again after the catalog has been refreshed."
        )


def meeting_kind(days: str | None, start: time | None, end: time | None) -> str:
    if days is None and start is None and end is None:
        return "async"
    if (
        isinstance(days, str) and days and set(days) <= DAY_OFFSETS.keys()
        and len(set(days)) == len(days)
        and isinstance(start, time) and isinstance(end, time)
        and start.tzinfo is None and end.tzinfo is None and start < end
    ):
        return "timed"
    return "invalid"
