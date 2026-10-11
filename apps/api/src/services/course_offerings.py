"""Recurring offering rules for degree planning.

Unknown offerings remain unrestricted; absence from one scraped term and the
semester in a sample curriculum do not establish a recurring restriction.
Explicit confirmations override patterns inferred from complete term inventories.
"""
from dataclasses import dataclass

from src.schemas.plan import ParseValidationError
from src.services.offering_history import course_history

PLANNING_SEASONS = frozenset({"10", "90"})  # Spring and fall; plans omit summer.


@dataclass(frozen=True)
class OfferingPattern:
    seasons: frozenset[str]
    source: str


COURSE_OFFERINGS = {
    "PHYS485": OfferingPattern(
        frozenset({"10"}),
        "Project owner confirms spring-only offering, 2026-09-28.",
    ),
}


def unavailable_offering_reason(code: str, term: str) -> str | None:
    if code in COURSE_OFFERINGS:
        return None
    record = course_history(code, term)
    if record and record['category'] == 'inactive':
        return f"{code} has not appeared in the recent three-year offering window and is treated as unavailable."
    if record and record['category'] == 'summer_only':
        return f"{code} has only recurring summer offerings; this plan includes fall and spring semesters."
    return None


def common_offering_seasons(codes: list[str | None], term: str | None = None) -> frozenset[str]:
    seasons = PLANNING_SEASONS
    for code in codes:
        pattern = COURSE_OFFERINGS.get(code)
        if pattern is not None:
            if not pattern.seasons or not pattern.seasons <= PLANNING_SEASONS:
                raise ValueError(f"Invalid offering seasons for {code}")
            seasons = seasons & pattern.seasons
        elif (record := course_history(code, term)) is not None:
            seasons = seasons & frozenset(record['planning_seasons'])
    return seasons


def require_common_offering(codes: list[str | None], term: str | None = None) -> frozenset[str]:
    seasons = common_offering_seasons(codes, term)
    if not seasons:
        raise ParseValidationError(
            "courses", "Courses that must be taken together have no shared offering semester: "
            + ", ".join(code for code in codes if code) + ". Review their offering and corequisite rules.",
        )
    return seasons
