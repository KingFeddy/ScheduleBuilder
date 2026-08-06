"""
Schedule solver — S2 implementation.

Replaces the S1 brute-force itertools.product with an iterative backtracking
CSP solver: MRV variable ordering, time-budget enforcement, professor whitelist
filtering, per-course failure diagnosis, and post-solve conflict validation as
an independent safety net.
"""
from __future__ import annotations
import logging
import time as time_module
from typing import Optional

from .models import SectionSlot
from .conflicts import sections_conflict, passes_commuter_filters, validate_schedule
from .gap import compute_gap_minutes, compute_gap_count, compute_campus_days
from .config import MAX_RESULTS, EXPLORE_LIMIT, SOLVE_TIME_BUDGET_MS, NODE_CHECK_INTERVAL
from ..schemas.schedule import (
    CommuterOptions,
    SolveResponse,
    ScheduleResult,
    SolveSectionResponse,
    MeetingResponse,
)

logger = logging.getLogger(__name__)


# ── Professor whitelist helpers ───────────────────────────────────────────────

def _normalize_professor(name: str) -> str:
    return name.strip().lower()


def _professor_matches_whitelist(
    professor_name: Optional[str],
    whitelist: list[str],
) -> bool:
    """
    True if whitelist is empty (any professor accepted) OR professor_name
    case-insensitively matches any entry. None professor_name never matches
    a non-empty whitelist.
    """
    if not whitelist:
        return True
    if professor_name is None:
        return False
    normalized = _normalize_professor(professor_name)
    return any(_normalize_professor(w) == normalized for w in whitelist)


def _build_filter_warning(
    course_code: str,
    all_sections: list[SectionSlot],
    after_professor: list[SectionSlot],
    after_commuter: list[SectionSlot],
    professor_whitelist: list[str],
) -> str:
    """Produce a specific, actionable warning for a zero-candidate course."""
    if not all_sections:
        return (
            f"{course_code}: no sections found for this term — verify the course code."
        )

    if not after_professor and professor_whitelist:
        actual = list({s.professor_name for s in all_sections if s.professor_name})[:3]
        return (
            f"{course_code}: no sections taught by {professor_whitelist} this term. "
            f"Professors teaching this course: {actual}. "
            f"Remove the professor preference or choose from the list above."
        )

    if not after_commuter:
        return (
            f"{course_code}: all sections conflict with your commuter constraints. "
            f"Try relaxing your time bounds or unblocking a day."
        )

    return f"{course_code}: no valid sections available."


# ── Serialisation ─────────────────────────────────────────────────────────────

def _section_to_response(section: SectionSlot) -> SolveSectionResponse:
    return SolveSectionResponse(
        crn=section.crn,
        term=section.term,
        course_code=section.course_code,
        professor_name=section.professor_name,
        total_seats=section.total_seats,
        open_seats=section.open_seats,
        scraped_at=section.scraped_at.isoformat() if section.scraped_at else None,
        meetings=[
            MeetingResponse(
                days=m.days,
                start_time=m.start_time.strftime("%H:%M") if m.start_time else None,
                end_time=m.end_time.strftime("%H:%M") if m.end_time else None,
                location=m.location,
            )
            for m in section.meetings
        ],
        section_number=section.section_number,
    )


# ── Iterative backtracking ────────────────────────────────────────────────────

def _backtrack(
    courses: list[str],
    candidates: dict[str, list[SectionSlot]],
    results: list[list[SectionSlot]],
    deadline_ns: int,
    nodes_checked: list[int],
) -> bool:
    """
    Iterative backtracking over the ordered course list.
    Returns True if the wall-clock time budget was exceeded.

    Stack item: (course_index, section_index, current_assignment)

    The backtrack point (same course, next section) is pushed BEFORE the
    forward step (next course, index 0). Because the stack is LIFO, the
    forward step is popped and executed first — identical to recursive DFS.

    `current_assignment + [candidate]` always produces a NEW list for the
    forward push, so there is no aliasing between sibling branches.
    """
    stack: list[tuple[int, int, list[SectionSlot]]] = [(0, 0, [])]

    while stack:
        course_idx, section_idx, current_assignment = stack.pop()

        nodes_checked[0] += 1
        if nodes_checked[0] % NODE_CHECK_INTERVAL == 0:
            if time_module.monotonic_ns() > deadline_ns:
                return True  # time budget exceeded

        # ── Complete assignment ───────────────────────────────────────────
        if course_idx == len(courses):
            violations = validate_schedule(current_assignment)
            if violations:
                # This fires only if sections_conflict has a bug. Log loudly.
                logger.error(
                    "[SOLVER BUG] post-solve validation caught conflict that "
                    "backtracker missed: %s",
                    violations,
                )
                continue
            results.append(list(current_assignment))
            if len(results) >= EXPLORE_LIMIT:
                return False
            continue

        # ── Extend assignment ─────────────────────────────────────────────
        course_code = courses[course_idx]
        sections    = candidates[course_code]

        if section_idx >= len(sections):
            continue  # exhausted all sections for this course — backtrack

        # Push backtrack point first so the forward step executes first (LIFO)
        stack.append((course_idx, section_idx + 1, current_assignment))

        candidate = sections[section_idx]
        conflict  = any(
            sections_conflict(candidate, assigned)
            for assigned in current_assignment
        )

        if not conflict:
            # New list — no aliasing with the backtrack branch above
            stack.append((course_idx + 1, 0, current_assignment + [candidate]))

    return False  # exhausted search space within budget


# ── Public solver API ─────────────────────────────────────────────────────────

def solve(
    course_codes: list[str],
    sections_by_course: dict[str, list[SectionSlot]],
    options: CommuterOptions,
    professor_preferences: dict[str, list[str]],
    compact_week: bool = False,
) -> SolveResponse:
    """
    Pure synchronous solver. Called from the route handler after DB loading.

    Args:
        course_codes:          Deduplicated list validated by SolveRequest.
        sections_by_course:    Output of load_sections_with_meetings (2-query bulk load).
        options:               Commuter constraints (Pydantic CommuterOptions).
        professor_preferences: Per-course professor whitelists. Absent key = any professor.

    Returns:
        SolveResponse with ranked schedules, warnings, and truncation flag.
    """
    warnings: list[str] = []

    # ── Phase 1: Filter ────────────────────────────────────────────────────
    candidates: dict[str, list[SectionSlot]] = {}
    for code in course_codes:
        all_secs      = sections_by_course.get(code, [])
        prof_whitelist = professor_preferences.get(code, [])

        after_prof    = [
            s for s in all_secs
            if _professor_matches_whitelist(s.professor_name, prof_whitelist)
        ]
        after_commuter = [
            s for s in after_prof
            if passes_commuter_filters(s, options)
        ]

        if not after_commuter:
            warnings.append(
                _build_filter_warning(code, all_secs, after_prof, after_commuter, prof_whitelist)
            )
            return SolveResponse(results=[], warnings=warnings, truncated=False)

        candidates[code] = after_commuter

    # ── Phase 2: MRV ordering ─────────────────────────────────────────────
    # Trying the most constrained course (fewest valid sections) first prunes
    # the search tree earliest, dramatically reducing nodes explored.
    ordered_courses = sorted(course_codes, key=lambda c: len(candidates[c]))

    # ── Phase 3: Backtrack ─────────────────────────────────────────────────
    deadline_ns    = time_module.monotonic_ns() + SOLVE_TIME_BUDGET_MS * 1_000_000
    nodes_checked  = [0]
    raw_results:   list[list[SectionSlot]] = []

    truncated = _backtrack(ordered_courses, candidates, raw_results, deadline_ns, nodes_checked)

    logger.info(
        "[SOLVER] courses=%d nodes=%d results=%d truncated=%s",
        len(course_codes), nodes_checked[0], len(raw_results), truncated,
    )

    if truncated:
        warnings.append(
            f"Schedule search hit the time limit. "
            f"Showing {len(raw_results)} of potentially more valid schedules."
        )

    if not raw_results:
        pair = _find_impossible_pair(ordered_courses, candidates)
        if pair:
            a, b = pair
            warnings.append(
                f"No valid schedule exists: {a} and {b} have no compatible sections this term."
            )
        else:
            warnings.append("No valid schedules found.")
        return SolveResponse(results=[], warnings=warnings, truncated=truncated)

    # ── Compact Week filter ────────────────────────────────────────────────
    if compact_week:
        raw_results = [r for r in raw_results if compute_campus_days(r) < 5]
        if not raw_results:
            warnings.append(
                "No schedules found with at least one day off. Try disabling Compact Week."
            )
            return SolveResponse(results=[], warnings=warnings, truncated=truncated)

    # ── Phase 4: Rank ──────────────────────────────────────────────────────
    def _rank_key(sections: list[SectionSlot]) -> tuple:
        gap_count  = compute_gap_count(sections)
        gap        = compute_gap_minutes(sections)
        days       = compute_campus_days(sections)
        total_open = sum(s.open_seats for s in sections)
        if compact_week:
            return (days, gap_count, gap, -total_open)
        elif options.minimize_gaps:
            return (gap_count, gap, days, -total_open)
        else:
            return (days, gap, -total_open)

    raw_results.sort(key=_rank_key)
    top_results = raw_results[:MAX_RESULTS]

    # ── Phase 5: Serialise ─────────────────────────────────────────────────
    results = [
        ScheduleResult(
            sections=[_section_to_response(s) for s in section_list],
            gap_minutes=compute_gap_minutes(section_list),
            campus_days=compute_campus_days(section_list),
            has_async_sections=any(s.is_async for s in section_list),
        )
        for section_list in top_results
    ]

    return SolveResponse(results=results, warnings=warnings, truncated=truncated)


def _find_impossible_pair(
    courses: list[str],
    candidates: dict[str, list[SectionSlot]],
) -> tuple[str, str] | None:
    """
    Find the first pair of courses with zero compatible section combinations.
    Only called after backtracking returns no results — never on the hot path.
    O(n² × m²): at most 8²/2 × 15² = 3,600 comparisons — negligible.
    """
    for i, course_a in enumerate(courses):
        for course_b in courses[i + 1:]:
            has_compatible = any(
                not sections_conflict(sa, sb)
                for sa in candidates[course_a]
                for sb in candidates[course_b]
            )
            if not has_compatible:
                return (course_a, course_b)
    return None
