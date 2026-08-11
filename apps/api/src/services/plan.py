from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.plan import (
    COURSE_CODE_PATTERN,
    WILDCARD_PATTERN,
    ParsedDegree,
    ParsedDegreeValidated,
    ParseValidationError,
    StillNeededItem,
)
from src.scheduler.time_utils import (
    get_next_njit_term,
    get_planning_terms,
    term_to_label,
)

logger = logging.getLogger(__name__)

CREDIT_CONSISTENCY_TOLERANCE = 6
MIN_CREDITS_PER_SEMESTER = 3
MAX_CREDITS_PER_SEMESTER = 24


# ── Wildcard matching ─────────────────────────────────────────────────────────

def matches_wildcard(course_code: str, pattern: str) -> bool:
    """
    Returns True if course_code matches the wildcard pattern.

    Supported forms (normalized upstream by dw_parser):
      PHYS3XX → any PHYS 3xx course  (two trailing Xs = two digit positions)
      CS4XX   → any CS 4xx course
      @       → any course (universal wildcard)

    Each X matches exactly one digit. PHYS3XX → ^PHYS3\\d\\d$.
    Patterns with embedded @ (e.g. PHYS3@) should have been normalized to XX
    by the parser before reaching this function.
    """
    if pattern == "@":
        return True

    code = course_code.strip().upper()
    pat  = pattern.strip().upper()

    if "X" not in pat and "@" not in pat:
        return code == pat

    regex = "^" + re.sub(r"X", r"\\d", pat) + "$"
    try:
        return bool(re.match(regex, code))
    except re.error:
        return False


def find_matching_requirement(
    elective_code: str,
    still_needed: list[StillNeededItem],
    already_satisfied: set[int],
) -> int | None:
    """
    Returns the index of the first still_needed item whose options[] match
    elective_code. Exact matches take priority over wildcard matches.
    Returns None if no unsatisfied requirement matches.
    """
    elective_upper = elective_code.strip().upper()

    # Phase 1: exact match
    for i, item in enumerate(still_needed):
        if i in already_satisfied:
            continue
        if elective_upper in item.options:
            return i

    # Phase 2: wildcard match
    for i, item in enumerate(still_needed):
        if i in already_satisfied:
            continue
        if any(matches_wildcard(elective_upper, opt) for opt in item.options):
            return i

    return None


# ── Credit + title lookup ─────────────────────────────────────────────────────

async def get_course_credits_and_titles(
    session: AsyncSession,
    course_codes: list[str],
) -> dict[str, tuple[int, str | None]]:
    """
    Returns {course_code: (credits, title)} for all known codes in one query.
    Defaults to (3, None) for unknown courses.

    One ANY(:codes) round-trip replaces the N+1 per-course title lookups that
    the original design would have made inside the semester assignment loop.
    """
    if not course_codes:
        return {}

    result = await session.execute(
        text(
            "SELECT course_code, credits, title FROM courses"
            " WHERE course_code = ANY(:codes)"
        ),
        {"codes": course_codes},
    )
    data: dict[str, tuple[int, str | None]] = {
        row["course_code"]: (row["credits"], row["title"])
        for row in result.mappings()
    }

    for code in course_codes:
        if code not in data:
            logger.warning("Course %r not found in courses table — defaulting to 3 credits", code)
            data[code] = (3, None)

    return data


def validate_parsed_degree(raw: ParsedDegree) -> ParsedDegreeValidated:
    """
    Business-logic validation. Raises ParseValidationError on violations.
    Only a ParsedDegreeValidated should be returned to the client or passed
    to the planner. Raw ParsedDegree is never trusted downstream.
    """
    if not raw.majors:
        raise ParseValidationError(
            "majors",
            "No major detected. Please verify this is a DegreeWorks degree audit PDF.",
        )

    if all(
        x is not None
        for x in [raw.credits_completed, raw.credits_required, raw.credits_remaining]
    ):
        computed = raw.credits_required - raw.credits_completed  # type: ignore[operator]
        delta = abs(raw.credits_remaining - computed)  # type: ignore[operator]
        if delta > CREDIT_CONSISTENCY_TOLERANCE:
            raise ParseValidationError(
                "credits",
                f"Credit counts are inconsistent: {raw.credits_completed} completed + "
                f"{raw.credits_remaining} remaining ≠ {raw.credits_required} required "
                f"(delta: {delta}). The PDF may not be a DegreeWorks audit.",
            )

    if raw.credits_required is not None and not (100 <= raw.credits_required <= 160):
        raise ParseValidationError(
            "credits_required",
            f"credits_required={raw.credits_required} is outside the plausible NJIT "
            f"range (100–160).",
        )

    # Filter completed_courses: keep valid NJIT codes, log and drop the rest.
    # AP credit and transfer credit lines produce non-standard codes (ENG121, CHEM5)
    # that the scraper never saw — drop them rather than letting them pollute the plan.
    valid_completed: list[str] = []
    for code in raw.completed_courses:
        if COURSE_CODE_PATTERN.match(code):
            valid_completed.append(code)
        elif WILDCARD_PATTERN.search(code):
            logger.warning("wildcard in completed_courses: %r — skipping", code)
        else:
            logger.warning(
                "non-standard code in completed_courses: %r (AP/transfer credit?)", code
            )

    valid_in_progress: list[str] = []
    for code in raw.in_progress_courses:
        if COURSE_CODE_PATTERN.match(code):
            valid_in_progress.append(code)
        else:
            logger.warning(
                "non-standard code in in_progress_courses: %r — skipping", code
            )

    for item in raw.still_needed:
        for code in item.options:
            if not WILDCARD_PATTERN.search(code) and not COURSE_CODE_PATTERN.match(code):
                logger.warning(
                    "non-standard code in still_needed: %r (%r)", code, item.requirement
                )

    if (
        raw.credits_remaining is not None
        and raw.credits_remaining > 15
        and len(raw.still_needed) < 2
    ):
        logger.warning(
            "credits_remaining=%d but only %d still_needed items — "
            "regex may have missed requirement blocks",
            raw.credits_remaining,
            len(raw.still_needed),
        )

    return ParsedDegreeValidated(
        student_name=raw.student_name,
        majors=raw.majors,
        minors=raw.minors,
        catalog_year=raw.catalog_year,
        credits_completed=raw.credits_completed,
        credits_required=raw.credits_required,
        credits_remaining=raw.credits_remaining,
        completed_courses=valid_completed,
        in_progress_courses=valid_in_progress,
        still_needed=raw.still_needed,
    )


# ── Planner output types ──────────────────────────────────────────────────────

@dataclass
class PlannedCourse:
    course_code: str
    title:       str | None
    credits:     int
    badge:       str        # "Required" | "Elective" | "TBD"
    reason:      str


@dataclass
class SemesterCard:
    term:          str      # e.g. "202710"
    term_label:    str      # e.g. "Spring 2027"
    courses:       list[PlannedCourse] = field(default_factory=list)
    total_credits: int = 0


@dataclass
class GeneratedPlan:
    semesters:            list[SemesterCard]
    projected_graduation: str
    warnings:             list[str]


# ── Internal resolved-item type ───────────────────────────────────────────────

@dataclass
class _ResolvedItem:
    requirement: str
    course_code: str | None   # None = TBD
    credits:     int = 3
    title:       str | None = None
    badge:       str = "Required"   # "Required" | "Elective" | "TBD"
    reason:      str = ""


# ── Option selection ──────────────────────────────────────────────────────────

async def select_best_option(
    item: StillNeededItem,
    completed: set[str],
    in_progress: set[str],
    student_electives: list[str],
    target_term: str,
    session: AsyncSession,
) -> str | None:
    """
    Returns the best concrete course code for a still_needed requirement,
    or None if only wildcards are available (TBD slot).

    Priority:
      1. A student-added elective that appears in options
      2. An option that has sections in target_term
      3. First non-wildcard, non-excluded option
    """
    available = [
        opt for opt in item.options
        if opt not in completed
        and opt not in in_progress
        and not WILDCARD_PATTERN.search(opt)
    ]

    if not available:
        return None

    # Priority 1: student elective that's an explicit option
    for elective in student_electives:
        if elective in available:
            return elective

    # Priority 2: option with scraped sections in the target term
    result = await session.execute(
        text(
            "SELECT DISTINCT course_code FROM sections"
            " WHERE course_code = ANY(:codes) AND term = :term"
        ),
        {"codes": available, "term": target_term},
    )
    offered = {row["course_code"] for row in result.mappings()}
    for opt in available:
        if opt in offered:
            return opt

    # Priority 3: first available
    return available[0]


def _validate_credit_target(preferences: dict) -> int:
    """
    preferences is a raw, untyped dict from a public, unauthenticated API —
    credits_per_semester must be checked here, not assumed to already be a
    sane int just because every legitimate UI control sends one.
    """
    raw = preferences.get("credits_per_semester", 15)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ParseValidationError(
            "credits_per_semester", f"must be a whole number, got {raw!r}."
        )
    if not (MIN_CREDITS_PER_SEMESTER <= raw <= MAX_CREDITS_PER_SEMESTER):
        raise ParseValidationError(
            "credits_per_semester",
            f"must be between {MIN_CREDITS_PER_SEMESTER} and "
            f"{MAX_CREDITS_PER_SEMESTER}, got {raw}.",
        )
    return raw


# ── Main planner ──────────────────────────────────────────────────────────────

async def generate_plan(
    validated: ParsedDegreeValidated,
    preferences: dict,
    session: AsyncSession,
) -> GeneratedPlan:
    """
    Produces a semester-by-semester plan from a validated ParsedDegree.

    preferences keys:
      courses (list[str])        — student-chosen electives
      credits_per_semester (int) — MIN_CREDITS_PER_SEMESTER..MAX_CREDITS_PER_SEMESTER
    """
    warnings: list[str] = []

    credit_target: int = _validate_credit_target(preferences)
    student_electives: list[str] = [
        e.strip().upper() for e in preferences.get("courses", [])
    ]

    completed   = set(validated.completed_courses)
    in_progress = set(validated.in_progress_courses)
    all_excluded = completed | in_progress

    # ── 1. Early exit: already graduated ─────────────────────────────────────

    if not validated.still_needed and (validated.credits_remaining or 0) == 0:
        return GeneratedPlan(
            semesters=[],
            projected_graduation="This semester",
            warnings=["You've completed all degree requirements. Congratulations!"],
        )

    # ── 2. Filter student electives ───────────────────────────────────────────

    electives_to_place: list[str] = []
    for code in student_electives:
        if code in all_excluded:
            warnings.append(
                f"{code} is already completed or in progress — removed from elective list."
            )
        else:
            electives_to_place.append(code)

    # ── 3. Resolve still_needed → concrete courses ────────────────────────────

    planning_terms = get_planning_terms(n=10)
    current_term   = planning_terms[0]

    resolved: list[_ResolvedItem] = []
    satisfied_indices: set[int] = set()

    # Match student electives to requirements (exact first, then wildcard)
    elective_to_req: dict[str, int] = {}   # elective code → still_needed index
    for elective in electives_to_place:
        idx = find_matching_requirement(elective, validated.still_needed, satisfied_indices)
        if idx is not None:
            satisfied_indices.add(idx)
            elective_to_req[elective] = idx

    # Build resolved items
    for i, item in enumerate(validated.still_needed):
        if i in satisfied_indices:
            code = next(e for e, idx in elective_to_req.items() if idx == i)
            resolved.append(_ResolvedItem(
                requirement=item.requirement,
                course_code=code,
                badge="Elective",
                reason=f"Your elective {code} satisfies '{item.requirement}'",
            ))
        else:
            best = await select_best_option(
                item, completed, in_progress, electives_to_place, current_term, session
            )
            resolved.append(_ResolvedItem(
                requirement=item.requirement,
                course_code=best,
                badge="Required" if best else "TBD",
                reason=(
                    f"Required for {validated.majors[0]}" if best
                    else f"Requirement '{item.requirement}' — discuss with advisor."
                ),
            ))

    # Add unmatched electives as extra courses
    for code in electives_to_place:
        if code not in elective_to_req:
            resolved.append(_ResolvedItem(
                requirement="Elective",
                course_code=code,
                badge="Elective",
                reason="Additional elective you requested",
            ))

    # ── 4. Fetch credits + titles in one batched query ────────────────────────

    all_codes = [r.course_code for r in resolved if r.course_code]
    course_data = await get_course_credits_and_titles(session, all_codes)

    for r in resolved:
        if r.course_code:
            credits, title = course_data.get(r.course_code, (3, None))
            r.credits = credits
            r.title   = title

    # ── 5. Detect credit overflow ─────────────────────────────────────────────

    total_planned = sum(r.credits for r in resolved)
    available_credits = validated.credits_remaining or 0

    if total_planned > available_credits + 6:
        warnings.append(
            f"Your plan requires approximately {total_planned} credits, "
            f"but your remaining credits are listed as {available_credits}. "
            f"Some requirements may double-count. Verify with your advisor."
        )

    # ── 6. Sort: concrete items by credits desc, TBD items last ──────────────

    concrete = [r for r in resolved if r.course_code is not None]
    tbd      = [r for r in resolved if r.course_code is None]
    concrete.sort(key=lambda r: -r.credits)

    items_pool = concrete + tbd

    # ── 7. Assign to semesters ────────────────────────────────────────────────

    semesters: list[SemesterCard] = []
    term_idx = 0

    while items_pool:
        if term_idx >= len(planning_terms):
            last = planning_terms[-1]
            for _ in range(5):
                last = get_next_njit_term(last)
                if not last.endswith("50"):
                    planning_terms.append(last)

        term = planning_terms[term_idx]
        card = SemesterCard(term=term, term_label=term_to_label(term))
        credits_used = 0
        remaining: list[_ResolvedItem] = []

        for item in items_pool:
            if credits_used + item.credits <= credit_target:
                card.courses.append(PlannedCourse(
                    course_code=item.course_code or "TBD",
                    title=item.title,
                    credits=item.credits,
                    badge=item.badge,
                    reason=item.reason,
                ))
                credits_used += item.credits
            else:
                remaining.append(item)

        # Force-add if nothing fit (single course exceeds credit_target)
        if not card.courses and items_pool:
            forced = items_pool[0]
            card.courses.append(PlannedCourse(
                course_code=forced.course_code or "TBD",
                title=forced.title,
                credits=forced.credits,
                badge=forced.badge,
                reason=forced.reason,
            ))
            credits_used = forced.credits
            remaining    = items_pool[1:]

        card.total_credits = credits_used
        semesters.append(card)
        items_pool = remaining
        term_idx  += 1

    # ── 8. Prerequisite disclaimer ────────────────────────────────────────────

    warnings.append(
        "This plan does not verify course prerequisites or semester availability. "
        "Confirm all prerequisites are met before registering."
    )

    return GeneratedPlan(
        semesters=semesters,
        projected_graduation=semesters[-1].term_label if semesters else "Unknown",
        warnings=warnings,
    )
