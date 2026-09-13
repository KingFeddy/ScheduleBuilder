from __future__ import annotations
from datetime import time
import re

from src.terms import TERM_CODE_PATTERN

# Minute-of-week offsets: each day starts this many minutes after Monday 00:00.
# Monday 10:00 = 600. Wednesday 10:00 = 3480. Day separation falls out of the math.
DAY_OFFSETS: dict[str, int] = {
    'M': 0,
    'T': 1440,
    'W': 2880,
    'R': 4320,
    'F': 5760,
    'S': 7200,
    'U': 8640,
}


def to_minute_intervals(
    days: str | None,
    start: time | None,
    end: time | None,
) -> list[tuple[int, int]]:
    """
    Expand one meeting pattern into (start_mow, end_mow) integer intervals,
    one per day the meeting occurs. Returns [] for async sections.
    """
    if not days or start is None or end is None:
        return []
    start_min = start.hour * 60 + start.minute
    end_min   = end.hour * 60 + end.minute
    return [
        (DAY_OFFSETS[d] + start_min, DAY_OFFSETS[d] + end_min)
        for d in days
        if d in DAY_OFFSETS
    ]


def intervals_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """
    True iff two MOW intervals overlap. Back-to-back (a[1] == b[0]) does NOT conflict.

    Predicate: a[0] < b[1] AND b[0] < a[1]
    """
    return a[0] < b[1] and b[0] < a[1]


def parse_hhmm(s: str) -> time:
    """Parse 'HH:MM' string to datetime.time. Used by commuter filters."""
    h, m = s.split(':')
    return time(int(h), int(m))


# ── NJIT term utilities ───────────────────────────────────────────────────────
# Term code format: YYYY + suffix  (e.g. 202690 = Fall 2026)
# Season suffixes describe terms, not date-based registration boundaries.
# The caller supplies the configured start; never infer it from today's month.

_SUFFIX_TO_SEASON = {"10": "Spring", "50": "Summer", "90": "Fall"}


def get_next_njit_term(term: str) -> str:
    """
    Returns the next academic term code.
    202690 (Fall 2026)   → 202710 (Spring 2027)
    202710 (Spring 2027) → 202750 (Summer 2027)
    202750 (Summer 2027) → 202790 (Fall 2027)
    """
    if not re.fullmatch(TERM_CODE_PATTERN, term):
        raise ValueError("Invalid NJIT term code.")
    year   = int(term[:4])
    suffix = term[4:]

    if suffix == "10":    # Spring → Summer
        return f"{year:04d}50"
    elif suffix == "50":  # Summer → Fall
        return f"{year:04d}90"
    else:                 # Fall → Spring next year
        if year == 9999:
            raise ValueError("The next term exceeds the supported four-digit year.")
        return f"{year + 1:04d}10"


def get_planning_terms(n: int, skip_summer: bool = True, *, start_term: str) -> list[str]:
    """
    Returns the next N planning terms starting from an explicit term.
    Skips Summer terms by default — most students don't plan for summer.
    """
    terms: list[str] = []
    if not re.fullmatch(TERM_CODE_PATTERN, start_term):
        raise ValueError("Invalid NJIT planning start term.")
    current = start_term

    while len(terms) < n:
        if not (skip_summer and current.endswith("50")):
            terms.append(current)
        if len(terms) < n:
            current = get_next_njit_term(current)

    return terms


def term_to_label(term: str) -> str:
    """202690 → 'Fall 2026',  202710 → 'Spring 2027'"""
    year   = term[:4]
    suffix = term[4:]
    season = _SUFFIX_TO_SEASON.get(suffix, "Unknown")
    return f"{season} {year}"
